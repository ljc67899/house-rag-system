# -*- coding: utf-8 -*-
"""模块 E：房价走势时间序列分析

- 数据：house_price_trend 区域月度挂牌均价序列
- 方法：LSTM 深度学习时序预测（PyTorch）为主，Holt-Winters 指数平滑为对照基线
- 输出：历史序列 + 未来 N 个月预测 + 环比/同比指标 + LSTM 模型评估
"""

import os
from datetime import datetime
from loguru import logger
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.models.trend import HousePriceTrend

try:
    import numpy as np
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    _TORCH_OK = True
except ImportError:  # pragma: no cover
    _TORCH_OK = False


class _TrendLSTM(nn.Module):
    """房价月度均价 LSTM：滑动窗口 -> 一次直接输出未来 H 个月的三分位

    为什么改成「直接多步」：递归版把第 1 步的预测当成真实值喂回去预测第 2 步，
    12 步下来误差指数累积，末端基本失真。改成一个 fc 头直接吐 H 个点，误差不叠加。

    为什么输出三个分位：用户要的是「区间」而不是「一个数」。用分位数（pinball）
    损失一次训练出 P10/P50/P90，比事后加减固定百分比更像统计意义上的置信区间。
    """

    def __init__(self, input_size: int = 1, hidden_size: int = 32,
                 num_layers: int = 2, dropout: float = 0.2,
                 horizon: int = 12, quantiles: int = 3):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_size, horizon * quantiles)
        self.horizon = horizon
        self.quantiles = quantiles

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :]).view(-1, self.horizon, self.quantiles)


class TrendService:
    """房价走势时序预测（模块 E）：基线矩阵择优 + LSTM 直接多步 + 区间预测

    核心改动（相对早期版本）：
        1. 不再「torch 装了就用 LSTM」，而是跑一遍基线矩阵（naive / 季节朴素 /
           移动平均 / 线性趋势 / Holt-Winters / LSTM），按验证集 MAE 择优；
        2. Holt-Winters 的 alpha/beta/gamma 由网格搜索拟合，不再写死——否则基线
           被人为削弱，LSTM 会显得比实际更好；
        3. LSTM 由递归多步改为直接多步 + 分位数输出，同时给出 P10/P90 区间。
    """

    WINDOW = 12          # 滑窗长度：用过去 12 个月
    HORIZON = 12         # LSTM 直接输出未来 12 个月
    VALID_MONTHS = 12    # 验证集留出最后 12 个月
    QUANTILES = (0.1, 0.5, 0.9)
    LSTM_MODEL_DIR = os.path.join("data", "models", "trend_lstm")
    BASELINE_CACHE_DIR = os.path.join("data", "models", "trend_baseline")
    MODEL_VERSION = 2    # 模型结构变更时递增，旧缓存自动失效


    def __init__(self, db: Session):
        self.db = db
        self._lstm_cache = {}

    def regions(self) -> list:
        """可选区域列表（含最新均价），只返回合法行政区县"""
        from app.utils.regions import is_district

        self._ensure_fresh()

        rows = self.db.query(HousePriceTrend).all()
        latest = {}
        for r in rows:
            if not r.region or not is_district(r.region):
                continue
            if r.region not in latest or r.month > latest[r.region]["month"]:
                latest[r.region] = {"month": r.month, "avg_price": r.avg_price}
        return [{"name": k, "latest_price": round(v["avg_price"], 2), "month": v["month"]}
                for k, v in sorted(latest.items(), key=lambda kv: -kv[1]["avg_price"])]

    def get_trend(self, region: str, predict_months: int = 12) -> dict:
        """区域历史走势 + 未来预测"""
        self._ensure_fresh()
        rows = (self.db.query(HousePriceTrend)
                .filter(HousePriceTrend.region == region)
                .order_by(HousePriceTrend.month.asc()).all())
        if not rows:
            return {"status": "error", "error": f"区域 {region} 无数据", "region": region}

        months = [r.month for r in rows]
        values = [r.avg_price for r in rows]
        history = [{"month": m, "price": round(v, 2)} for m, v in zip(months, values)]

        forecast, method, model_metrics, interval = self._forecast(region, values, predict_months)
        # 预测月份必须落在「本月之后」：若历史序列末端还停在过去（数据未及时补齐），
        # 也从下个月开始排，避免把「未来预测」画到已经过去的月份上
        now_month = datetime.now().strftime("%Y-%m")
        base_month = months[-1] if months[-1] >= now_month else now_month
        pred_months = self._next_months(base_month, predict_months)

        lows = (interval or {}).get("low") or []
        highs = (interval or {}).get("high") or []
        predictions = []
        for i, (m, p) in enumerate(zip(pred_months, forecast)):
            item = {"month": m, "price": round(p, 2)}
            if i < len(lows) and i < len(highs):
                item["low"] = round(lows[i], 2)
                item["high"] = round(highs[i], 2)
            predictions.append(item)

        # 环比 / 同比
        mom = (values[-1] / values[-2] - 1) * 100 if len(values) >= 2 else 0
        yoy = (values[-1] / values[-13] - 1) * 100 if len(values) >= 13 else None
        avg_12 = sum(values[-12:]) / 12 if len(values) >= 12 else None

        board = self._select_best(region, values).get("board") or []

        return {
            "status": "ok",
            "region": region,
            "history": history,
            "predictions": predictions,
            "latest_price": round(values[-1], 2),
            "mom_change": round(mom, 2),
            "yoy_change": round(yoy, 2) if yoy is not None else None,
            "avg_last12": round(avg_12, 2) if avg_12 else None,
            "method": method,
            "model_metrics": model_metrics,
            # 基线矩阵：所有候选方法在同一验证集上的 MAE/MAPE，谁被选中、差多少一目了然
            "method_board": board,
            "interval_note": "区间为 80% 置信（LSTM 用分位数损失，基线用验证集残差标准差按步长放大）",
            "trend": "上涨" if forecast[-1] >= values[-1] else "下跌",
            "predicted_change": round((forecast[-1] / values[-1] - 1) * 100, 2),
        }

    # ---------- 预测算法 ----------

    def _ensure_fresh(self):
        """数据末端若停留在过去，先补齐到本月（模拟序列随系统时间推进）"""
        try:
            from app.services.seed_simulated_data import extend_price_trends_to_now
            extend_price_trends_to_now(self.db)
        except Exception as e:  # pragma: no cover
            logger.warning(f"Extend price trends failed: {e}")

    def _forecast(self, region: str, values, h: int):
        """返回 (forecast, method_label, metrics, interval)

        决策逻辑：先跑基线矩阵 → 按验证集 MAE 选最优 → 用最优方法出预测。
        不再「torch 装了就用 LSTM」——那叫拿技术选数据，不叫拿数据选模型。
        """
        choice = self._select_best(region, values)
        method = choice.get("method") or "naive"
        label = choice.get("label") or "朴素预测（上月延续）"
        metrics = choice.get("metrics")

        if method == "lstm":
            try:
                model, meta = self._load_or_train_lstm(region, values)
                point, low, high = self._lstm_predict(model, meta, h)
                return point, label, meta.get("metrics") or metrics, {"low": low, "high": high}
            except Exception as e:  # pragma: no cover
                logger.warning(f"LSTM 预测失败，回退 Holt-Winters: {e}")
                method, label = "hw", "Holt-Winters（LSTM 异常回退）"

        forecast = self._run_method(method, values, h)
        return forecast, label, metrics, self._residual_interval(values, method, forecast)

    # ---------- 基线矩阵与择优 ----------

    def _baseline_methods(self):
        """候选基线：统一签名 (values, h) -> list"""
        return [
            ("naive", "朴素预测（上月延续）", self._fc_naive),
            ("seasonal_naive", "季节朴素（去年同期值）", self._fc_seasonal_naive),
            ("ma3", "移动平均 MA(3)", lambda v, h: self._fc_ma(v, h, 3)),
            ("ma6", "移动平均 MA(6)", lambda v, h: self._fc_ma(v, h, 6)),
            ("ma12", "移动平均 MA(12)", lambda v, h: self._fc_ma(v, h, 12)),
            ("linear", "线性趋势外推", self._fc_linear),
            ("hw", "Holt-Winters（参数网格拟合）", self._fc_hw),
        ]

    def _method_fn(self, method: str):
        for name, _label, fn in self._baseline_methods():
            if name == method:
                return fn
        return None

    def _run_method(self, method: str, values, h: int):
        fn = self._method_fn(method)
        try:
            return fn(values, h) if fn else self._fc_naive(values, h)
        except Exception as e:  # pragma: no cover
            logger.warning(f"[趋势] {method} 预测失败，回退朴素: {e}")
            return self._fc_naive(values, h)

    def _select_best(self, region: str, values):
        """跑基线矩阵，按验证集 MAE 选最优（内存 + 磁盘两级缓存）"""
        signature = [self.MODEL_VERSION, region, len(values), round(float(values[-1]), 4)]
        sig_key = tuple(signature)

        mem = getattr(self, "_best_cache", None)
        if mem is None:
            mem = self._best_cache = {}
        if sig_key in mem:
            return mem[sig_key]

        cached = self._read_baseline_cache(region, signature)
        if cached:
            mem[sig_key] = cached
            return cached

        board = []
        for name, label, fn in self._baseline_methods():
            mae, mape = self._score(values, fn)
            if mae is None:
                continue
            board.append({"method": name, "label": label, "mae": mae, "mape": mape})

        # LSTM 要训练，代价最高，放最后；数据量不够就不让它参与
        if _TORCH_OK and len(values) >= self.WINDOW + self.VALID_MONTHS + 8:
            try:
                _model, meta = self._load_or_train_lstm(region, values)
                m = meta.get("metrics") or {}
                if m.get("mae") is not None:
                    board.append({
                        "method": "lstm",
                        "label": "LSTM（直接多步 + 分位数区间）",
                        "mae": m["mae"],
                        "mape": m.get("mape"),
                    })
            except Exception as e:  # pragma: no cover
                logger.warning(f"[趋势] LSTM 评估失败，不参与择优: {e}")

        if not board:
            return {"method": "naive", "label": "朴素预测（上月延续）", "metrics": None, "board": []}

        board.sort(key=lambda x: x["mae"])
        best = board[0]
        result = {
            "method": best["method"],
            "label": best["label"],
            "metrics": {"mae": best["mae"], "mape": best["mape"]},
            "board": board,
        }
        mem[sig_key] = result
        self._write_baseline_cache(region, signature, result)
        logger.info(
            f"[趋势] {region} 择优={best['method']}(MAE {best['mae']}) "
            f"board={[(b['method'], b['mae']) for b in board]}"
        )
        return result

    def _score(self, values, fn):
        """用最后 12 个月做验证集，返回 (MAE, MAPE)；样本不足返回 (None, None)

        训练段与验证段按时间切分、不随机打乱——时序数据打乱会造成未来信息泄漏。
        """
        n = len(values)
        h = self.VALID_MONTHS
        if n < h + 14:
            return None, None
        train, truth = values[: n - h], values[n - h:]
        try:
            pred = fn(train, h)
        except Exception:
            return None, None
        if not pred or len(pred) < h:
            return None, None
        pred = pred[:h]
        mae = sum(abs(p - t) for p, t in zip(pred, truth)) / h
        mape = sum(abs((p - t) / t) for p, t in zip(pred, truth) if t) / h * 100
        return round(mae, 3), round(mape, 3)

    def _residual_interval(self, values, method: str, forecast):
        """基线没有分布假设：用验证集残差标准差给 80% 近似区间，随预测步长放大"""
        n = len(values)
        h = self.VALID_MONTHS
        if n < h + 14:
            return None
        fn = self._method_fn(method)
        if fn is None:
            return None
        try:
            pred = fn(values[: n - h], h)
        except Exception:
            return None
        resid = [p - t for p, t in zip(pred[:h], values[n - h:])]
        if len(resid) < 2:
            return None
        mean_r = sum(resid) / len(resid)
        sd = (sum((r - mean_r) ** 2 for r in resid) / (len(resid) - 1)) ** 0.5
        scale = 1.28  # 双侧各留 10% → 80% 区间
        return {
            "low": [max(0.5, p - scale * sd * (1 + 0.06 * i)) for i, p in enumerate(forecast)],
            "high": [p + scale * sd * (1 + 0.06 * i) for i, p in enumerate(forecast)],
        }

    # ---------- 基线实现 ----------

    @staticmethod
    def _fc_naive(values, h):
        return [values[-1]] * h

    @staticmethod
    def _fc_seasonal_naive(values, h, season=12):
        if len(values) < season:
            return [values[-1]] * h
        n = len(values)
        return [values[n - season + (i % season)] for i in range(h)]

    @staticmethod
    def _fc_ma(values, h, window):
        window = max(1, min(window, len(values)))
        level = sum(values[-window:]) / window
        return [level] * h

    @staticmethod
    def _fc_linear(values, h):
        n = len(values)
        if n < 2:
            return [values[-1]] * h
        mx = (n - 1) / 2
        my = sum(values) / n
        slope = sum((x - mx) * (y - my) for x, y in enumerate(values))
        slope /= sum((x - mx) ** 2 for x in range(n)) or 1.0
        return [my + slope * (n - 1 + i - mx) for i in range(1, h + 1)]

    def _fc_hw(self, values, h):
        alpha, beta, gamma = self._fit_hw(values)
        return self._hw_forecast(values, h, alpha, beta, gamma)

    def _fit_hw(self, values):
        """网格搜索 alpha/beta/gamma，以验证集 MAE 最小为目标

        早期版本把系数写死成 0.6/0.15/0.4，等于让「不调参的基线」去和「调过参的
        LSTM」比，属于评测偏差。这里在同一验证集上拟合，保证比的是调过的基线。
        """
        cache = getattr(self, "_hw_params", None)
        if cache is None:
            cache = self._hw_params = {}
        key = (len(values), round(float(values[-1]), 4))
        if key in cache:
            return cache[key]

        n = len(values)
        h = self.VALID_MONTHS
        if n < h + 14:
            params = (0.3, 0.05, 0.3)
        else:
            train, truth = values[: n - h], values[n - h:]
            best = None
            for a in (0.1, 0.3, 0.5, 0.7, 0.9):
                for b in (0.01, 0.05, 0.15, 0.3):
                    for g in (0.1, 0.3, 0.5, 0.7):
                        try:
                            pred = self._hw_forecast(train, h, a, b, g)
                        except Exception:
                            continue
                        mae = sum(abs(p - t) for p, t in zip(pred, truth)) / h
                        if best is None or mae < best[0]:
                            best = (mae, (a, b, g))
            params = best[1] if best else (0.3, 0.05, 0.3)
        cache[key] = params
        return params

    @staticmethod
    def _hw_forecast(values, h, alpha, beta, gamma, season_len=12):
        """Holt-Winters 加法季节性指数平滑（系数由 _fit_hw 拟合后传入）"""
        n = len(values)
        if n < season_len + 2:
            k = (values[-1] - values[0]) / max(n - 1, 1)
            return [values[-1] + k * (i + 1) for i in range(h)]

        level = sum(values[:season_len]) / season_len
        trend = (sum(values[season_len:2 * season_len]) - sum(values[:season_len])) / season_len ** 2
        season = {i: values[i] - level for i in range(season_len)}

        for t in range(n):
            idx = t % season_len
            prev_level = level
            level = alpha * (values[t] - season.get(idx, 0)) + (1 - alpha) * (level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend
            season[idx] = gamma * (values[t] - level) + (1 - gamma) * season.get(idx, 0)

        return [max(0.5, level + i * trend + season.get((n - 1 + i) % season_len, 0))
                for i in range(1, h + 1)]

    # ---------- 择优结果缓存 ----------

    def _read_baseline_cache(self, region, signature):
        import json
        path = os.path.join(self.BASELINE_CACHE_DIR, f"{region}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("signature") == signature:
                return data.get("result")
        except Exception as e:  # pragma: no cover
            logger.warning(f"[趋势] 读取基线缓存失败: {e}")
        return None

    def _write_baseline_cache(self, region, signature, result):
        import json
        try:
            os.makedirs(self.BASELINE_CACHE_DIR, exist_ok=True)
            path = os.path.join(self.BASELINE_CACHE_DIR, f"{region}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"signature": signature, "result": result}, f, ensure_ascii=False, indent=2)
        except Exception as e:  # pragma: no cover
            logger.warning(f"[趋势] 写基线缓存失败: {e}")

    # ---------- LSTM ----------

    def _load_or_train_lstm(self, region: str, values):
        """内存/文件缓存 LSTM；数据或模型版本变化时自动重训"""
        signature = (self.MODEL_VERSION, region, len(values), round(float(values[-1]), 4))
        if signature in self._lstm_cache:
            return self._lstm_cache[signature]

        path = os.path.join(self.LSTM_MODEL_DIR, f"{region}.pt")
        if os.path.exists(path):
            try:
                bundle = torch.load(path, map_location="cpu", weights_only=False)
                if bundle.get("signature") == signature:
                    model = _TrendLSTM(horizon=bundle["horizon"],
                                       quantiles=len(bundle["quantiles"]))
                    model.load_state_dict(bundle["state_dict"])
                    model.eval()
                    self._lstm_cache[signature] = (model, bundle)
                    return model, bundle
            except Exception as e:  # pragma: no cover
                logger.warning(f"Load trend LSTM cache failed: {e}")

        model, meta = self._train_lstm(region, values)
        self._lstm_cache[signature] = (model, meta)
        return model, meta

    def _train_lstm(self, region: str, values):
        """区域 LSTM：min-max 归一化 + 滑窗 → 直接多步 + 分位数损失 + 早停

        与旧版的三个区别：
            1. 一个样本监督 horizon 个未来点（直接多步），不是只监督下一个月；
            2. 损失用 pinball（分位数）而不是 MSE，一次学出 P10/P50/P90；
            3. 指标里补 interval_coverage，用来检查区间是不是「真的覆盖了 80%」。
        """
        values_arr = np.asarray(values, dtype=np.float32)
        window, horizon = self.WINDOW, self.HORIZON
        if len(values_arr) < window + horizon + 2:
            raise ValueError(f"时序长度不足：{len(values_arr)} < {window + horizon + 2}")

        vmin, vmax = float(values_arr.min()), float(values_arr.max())
        span = (vmax - vmin) or 1.0
        norm = (values_arr - vmin) / span

        X, Y = [], []
        for i in range(len(norm) - window - horizon + 1):
            X.append(norm[i:i + window])
            Y.append(norm[i + window:i + window + horizon])
        X = np.stack(X)[:, :, None].astype(np.float32)
        Y = np.stack(Y).astype(np.float32)

        # 验证集取最后 12 个样本（时间上不重叠、不打乱，避免未来信息泄漏）
        n_val = min(self.VALID_MONTHS, max(len(X) // 5, 1))
        Xtr, Ytr = X[: len(X) - n_val], Y[: len(Y) - n_val]
        Xte, Yte = X[len(X) - n_val:], Y[len(Y) - n_val:]
        quantiles = list(self.QUANTILES)

        torch.manual_seed(42)
        model = _TrendLSTM(horizon=horizon, quantiles=len(quantiles))
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

        best_val, best_state, wait, patience, max_epochs = float("inf"), None, 0, 30, 300
        epochs_run = 0
        if len(Xtr) >= 8:
            tr_ds = TensorDataset(torch.tensor(Xtr), torch.tensor(Ytr))
            tr_loader = DataLoader(tr_ds, batch_size=16, shuffle=True)
            for epoch in range(max_epochs):
                epochs_run = epoch + 1
                model.train()
                for xb, yb in tr_loader:
                    optimizer.zero_grad()
                    loss = self._pinball_loss(model(xb), yb, quantiles)
                    loss.backward()
                    optimizer.step()
                if len(Yte) > 0:
                    model.eval()
                    with torch.no_grad():
                        val_loss = self._pinball_loss(
                            model(torch.tensor(Xte)), torch.tensor(Yte), quantiles
                        ).item()
                    if val_loss < best_val:
                        best_val = val_loss
                        best_state = {k: v.clone() for k, v in model.state_dict().items()}
                        wait = 0
                    else:
                        wait += 1
                        if wait >= patience:
                            break

        if best_state is None:
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        model.load_state_dict(best_state)
        model.eval()

        metrics = {
            "epochs": epochs_run,
            "val_loss": round(float(best_val), 6) if best_val != float("inf") else None,
            "train_samples": int(len(Xtr)),
            "horizon": horizon,
            "quantiles": quantiles,
        }
        if len(Yte) > 0:
            with torch.no_grad():
                pred_q = model(torch.tensor(Xte)).numpy()  # [B, H, Q]
            med = pred_q[:, :, 1] * span + vmin
            true = Yte * span + vmin
            metrics["mae"] = round(float(np.mean(np.abs(med - true))), 2)
            metrics["mape"] = round(
                float(np.mean(np.abs((med - true) / np.clip(true, 1, None))) * 100), 2)
            # 区间覆盖率：真值落在 [P10, P90] 的比例，目标约 80%
            # 先做与推理一致的单调化，否则覆盖率统计会被交叉区间污染
            med_n = pred_q[:, :, 1]
            lo = np.minimum(pred_q[:, :, 0], med_n) * span + vmin
            hi = np.maximum(pred_q[:, :, 2], med_n) * span + vmin
            metrics["interval_coverage"] = round(
                float(np.mean((true >= lo) & (true <= hi)) * 100), 1)

        meta = {
            "signature": (self.MODEL_VERSION, region, len(values), round(float(values[-1]), 4)),
            "state_dict": best_state,
            "vmin": vmin, "vmax": vmax, "span": span,
            "window": window,
            "horizon": horizon,
            "quantiles": quantiles,
            "last_window": norm[-window:].tolist(),
            "metrics": metrics,
        }
        os.makedirs(self.LSTM_MODEL_DIR, exist_ok=True)
        torch.save(meta, os.path.join(self.LSTM_MODEL_DIR, f"{region}.pt"))
        logger.info(f"Trend LSTM trained: region={region}, {metrics}")
        return model, meta

    @staticmethod
    def _pinball_loss(pred, target, quantiles):
        """分位数（pinball）损失：pred=[B,H,Q]，target=[B,H]

        对第 q 分位，低估按 (1-q) 罚、高估按 q 罚，从而让三个头分别收敛到
        P10 / P50 / P90，而不是像 MSE 一样全部收敛到条件均值。
        """
        q = torch.tensor(quantiles, dtype=torch.float32)
        err = target.unsqueeze(-1) - pred
        return torch.max((q - 1) * err, q * err).mean()

    @staticmethod
    def _lstm_predict(model, meta, h: int):
        """一次前向直接输出未来 h 个月的三分位，返回 (point, low, high)

        不递归：避免「用预测值预测下一个月」导致的误差指数累积。
        """
        window_vals = np.asarray(meta["last_window"], dtype=np.float32)[None, :, None]
        with torch.no_grad():
            pred = model(torch.tensor(window_vals)).numpy()[0]  # [H, Q]
        pred = pred * meta["span"] + meta["vmin"]

        # 分位数交叉（quantile crossing）：三个头是独立输出的，P10 可能高于 P50、
        # P90 可能低于 P50。这里按定义裁剪，保证 low <= point <= high，
        # 否则前端画带状图会出现上下界交叉的诡异形状。
        med = pred[:, 1]
        lo = np.minimum(pred[:, 0], med)
        hi = np.maximum(pred[:, 2], med)

        h = max(1, min(h, pred.shape[0]))
        point = [max(0.5, float(v)) for v in med[:h]]
        low = [max(0.5, float(v)) for v in lo[:h]]
        high = [float(v) for v in hi[:h]]
        return point, low, high

    @staticmethod
    def _next_months(last_month: str, n: int) -> list:
        """返回 last_month 之后的连续 n 个自然月（YYYY-MM），按自然月推进不分大小月"""
        dt = datetime.strptime(last_month + "-01", "%Y-%m-%d")
        return [(dt + relativedelta(months=i)).strftime("%Y-%m") for i in range(1, n + 1)]

    def overview(self) -> dict:
        """各区域最新均价与涨跌总览"""
        regions = self.regions()
        items = []
        for r in regions:
            t = self.get_trend(r["name"], predict_months=3)
            items.append({
                "region": r["name"],
                "latest_price": r["latest_price"],
                "mom_change": t.get("mom_change"),
                "trend": t.get("trend"),
            })
        items.sort(key=lambda kv: -kv["latest_price"])
        rising = sum(1 for x in items if x.get("trend") == "上涨")
        return {
            "regions": items,
            "rising_count": rising,
            "falling_count": len(items) - rising,
        }
