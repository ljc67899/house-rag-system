# -*- coding: utf-8 -*-
"""模块 A：房价智能预测（深度学习回归）

- 数据：house_info 挂牌数据（总价、面积、户型、区域、朝向、类型）
- 特征：
    数值特征：面积、室数、厅数、卫数（StandardScaler 标准化）
    类别特征：区域、朝向、房屋类型（LabelEncoder + Embedding 嵌入编码）
- 模型：MLP 多层感知机（PyTorch），类别 Embedding 拼接数值特征后进入全连接回归
- 训练：Adam 优化器、MSELoss（log1p 目标变换）、早停、80/20 划分评估
- 缓存：模型持久化到 data/models/price_predictor.pt，首次调用自动训练
"""

import os
import re
from loguru import logger
from sqlalchemy.orm import Session

from app.models.house import HouseInfo

try:
    import numpy as np
    import pandas as pd
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    from sklearn.preprocessing import StandardScaler, LabelEncoder
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import r2_score, mean_absolute_error, root_mean_squared_error
    _DL_OK = True
except ImportError:  # pragma: no cover
    _DL_OK = False


class _PriceMLP(nn.Module):
    """房价预测深度神经网络：类别特征 Embedding + 数值特征 + 全连接回归"""

    def __init__(self, num_numeric: int, cat_sizes, embed_dim: int = 8,
                 hidden=(256, 128), dropout: float = 0.2):
        super().__init__()
        self.embeddings = nn.ModuleList([nn.Embedding(sz, embed_dim) for sz in cat_sizes])
        input_dim = num_numeric + len(cat_sizes) * embed_dim
        layers = []
        dims = [input_dim] + list(hidden)
        for i in range(len(dims) - 1):
            layers += [nn.Linear(dims[i], dims[i + 1]), nn.ReLU(), nn.Dropout(dropout)]
        layers.append(nn.Linear(dims[-1], 1))
        self.net = nn.Sequential(*layers)

    def forward(self, numeric, cat_idx):
        embeds = [emb(cat_idx[:, i]) for i, emb in enumerate(self.embeddings)]
        x = torch.cat([numeric] + embeds, dim=1)
        return self.net(x).squeeze(-1)


class PricePredictionService:
    """基于深度学习的房价预测服务（模块 A）"""

    MODEL_DIR = os.path.join("data", "models")
    MODEL_PATH = os.path.join(MODEL_DIR, "price_predictor.pt")

    NUM_FEATURES = ["area", "room_num", "hall_num", "toilet_num"]
    CAT_FEATURES = ["region", "direction", "rent_type"]
    EMBED_DIM = 8
    HIDDEN = (256, 128)

    def __init__(self, db: Session):
        self.db = db
        self._model = None
        self._scaler = None
        self._encoders = None
        self._cat_sizes = None
        self._metrics = None

    # ---------- 数据加载与特征工程 ----------

    @staticmethod
    def _parse_price_wan(price_str):
        if not price_str:
            return None
        s = str(price_str).replace("万元", "").replace("万", "").replace(",", "").replace("，", "")
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_area(area_str):
        if not area_str:
            return None
        s = str(area_str).replace("平米", "").replace("㎡", "").replace("平", "").strip()
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_rooms(rooms_str):
        """'3室2厅1卫' -> (3, 2, 1)"""
        s = str(rooms_str or "")
        def _n(k):
            m = re.search(r"(\d+)" + k, s)
            return int(m.group(1)) if m else 0
        return _n("室"), _n("厅"), _n("卫")

    @staticmethod
    def _direction_group(direction: str) -> str:
        """朝向归一化：南北通透/朝南/朝东 -> 南 等"""
        d = direction or "未知"
        if "南北" in d or "南" in d:
            return "南"
        if "东" in d:
            return "东"
        if "西" in d:
            return "西"
        if "北" in d:
            return "北"
        return "未知"

    def _load_training_frame(self) -> "pd.DataFrame":
        rows = []
        houses = self.db.query(HouseInfo).all()
        for h in houses:
            p = self._parse_price_wan(h.price)
            a = self._parse_area(h.area)
            if p is None or a is None or not (50 < p < 5000) or not (20 <= a <= 400):
                continue
            room, hall, toilet = self._parse_rooms(h.rooms)
            rows.append({
                "area": a, "room_num": room, "hall_num": hall, "toilet_num": toilet,
                "region": (h.region or "未知").strip(),
                "direction": self._direction_group(h.direction) or "未知",
                "rent_type": (h.rent_type or "未知").strip(),
                "price": p,
            })
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    # ---------- 训练 ----------

    def ensure_trained(self, force: bool = False) -> dict:
        """加载缓存模型；不存在或强制时重新训练"""
        if _DL_OK and not force and os.path.exists(self.MODEL_PATH):
            try:
                bundle = torch.load(self.MODEL_PATH, map_location="cpu", weights_only=False)
                model = _PriceMLP(len(self.NUM_FEATURES), bundle["cat_sizes"],
                                  embed_dim=self.EMBED_DIM, hidden=self.HIDDEN)
                model.load_state_dict(bundle["state_dict"])
                model.eval()
                self._model = model
                self._scaler = bundle["scaler"]
                self._encoders = bundle["encoders"]
                self._cat_sizes = bundle["cat_sizes"]
                self._metrics = bundle["metrics"]
                return self._metrics
            except Exception as e:  # pragma: no cover
                logger.warning(f"Load model cache failed: {e}")

        metrics = self.train()
        return metrics

    def train(self) -> dict:
        """训练深度学习模型并缓存，返回评估指标"""
        if not _DL_OK:
            return {"status": "unavailable", "error": "缺少 PyTorch 依赖，请先 pip install torch"}
        df = self._load_training_frame()
        if df.empty:
            return {"status": "error", "error": "无有效训练数据"}

        X_num = df[self.NUM_FEATURES].values.astype(np.float32)
        y = np.log1p(df["price"].values).astype(np.float32)  # log 目标变换，抑制大价差影响

        # 数值特征标准化
        self._scaler = StandardScaler().fit(X_num)
        X_num_s = self._scaler.transform(X_num).astype(np.float32)

        # 类别特征编码（LabelEncoder + 索引）
        self._encoders = {}
        X_cat_idx = np.zeros((len(df), len(self.CAT_FEATURES)), dtype=np.int64)
        for j, f in enumerate(self.CAT_FEATURES):
            enc = LabelEncoder().fit(df[f].astype(str).values)
            self._encoders[f] = enc
            X_cat_idx[:, j] = enc.transform(df[f].astype(str).values)
        self._cat_sizes = [int(len(enc.classes_)) for enc in self._encoders.values()]

        # 80/20 划分
        Xn_tr, Xn_te, Xc_tr, Xc_te, y_tr, y_te = train_test_split(
            X_num_s, X_cat_idx, y, test_size=0.2, random_state=42)

        tr_ds = TensorDataset(torch.tensor(Xn_tr), torch.tensor(Xc_tr), torch.tensor(y_tr))
        te_ds = TensorDataset(torch.tensor(Xn_te), torch.tensor(Xc_te), torch.tensor(y_te))
        tr_loader = DataLoader(tr_ds, batch_size=256, shuffle=True)
        te_loader = DataLoader(te_ds, batch_size=1024)

        # 模型与优化器
        torch.manual_seed(42)
        device = torch.device("cpu")
        model = _PriceMLP(len(self.NUM_FEATURES), self._cat_sizes,
                          embed_dim=self.EMBED_DIM, hidden=self.HIDDEN).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        loss_fn = nn.MSELoss()

        # 早停训练
        best_val, best_state, wait, patience, max_epochs = float("inf"), None, 0, 25, 200
        for epoch in range(max_epochs):
            model.train()
            for xn, xc, yb in tr_loader:
                xn, xc, yb = xn.to(device), xc.to(device), yb.to(device)
                optimizer.zero_grad()
                loss = loss_fn(model(xn, xc), yb)
                loss.backward()
                optimizer.step()

            model.eval()
            val_loss, n = 0.0, 0
            with torch.no_grad():
                for xn, xc, yb in te_loader:
                    val_loss += loss_fn(model(xn.to(device), xc.to(device)),
                                        yb.to(device)).item() * len(yb)
                    n += len(yb)
            val_loss /= max(n, 1)
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
        self._model = model

        # 测试集评估（还原为原始价格）
        preds, trues = [], []
        with torch.no_grad():
            for xn, xc, yb in te_loader:
                p = torch.expm1(model(xn.to(device), xc.to(device)).cpu())
                preds.append(p.numpy())
                trues.append(np.expm1(yb.numpy()))
        y_pred = np.maximum(np.concatenate(preds), 1.0)
        y_true = np.concatenate(trues)

        metrics = {
            "status": "ok",
            "train_size": int(len(df)),
            "test_size": int(len(y_true)),
            "epochs": int(epoch) + 1,
            "best_val_loss": round(float(best_val), 4),
            "r2": round(float(r2_score(y_true, y_pred)), 4),
            "mae": round(float(mean_absolute_error(y_true, y_pred)), 2),
            "rmse": round(float(root_mean_squared_error(y_true, y_pred)), 2),
            "mape": round(float(np.mean(np.abs((y_true - y_pred) / np.clip(y_true, 1, None))) * 100), 2),
            "params": int(sum(p.numel() for p in model.parameters())),
            "train_at": None,
        }

        # 持久化
        os.makedirs(self.MODEL_DIR, exist_ok=True)
        torch.save({
            "state_dict": best_state,
            "scaler": self._scaler,
            "encoders": self._encoders,
            "cat_sizes": self._cat_sizes,
            "metrics": metrics,
        }, self.MODEL_PATH)
        metrics["train_at"] = os.path.getmtime(self.MODEL_PATH)
        self._metrics = metrics
        logger.info(
            f"Price DL model trained: R2={metrics['r2']}, MAE={metrics['mae']}, "
            f"MAPE={metrics['mape']}%, epochs={metrics['epochs']}")
        return metrics

    # ---------- 预测 ----------

    def predict(self, region: str, area: float, rooms: str = "2室1厅1卫",
                direction: str = "朝南", rent_type: str = "住宅") -> dict:
        """深度学习预测房价（万元）"""
        if not _DL_OK:
            return {"status": "unavailable", "error": "缺少 PyTorch 依赖，请先 pip install torch"}
        metrics = self.ensure_trained()
        if metrics.get("status") != "ok":
            return metrics

        room, hall, toilet = self._parse_rooms(rooms)
        num = np.array([[float(area), room, hall, toilet]], dtype=np.float32)
        cat_raw = {
            "region": str(region or "未知").strip(),
            "direction": self._direction_group(direction) or "未知",
            "rent_type": str(rent_type or "未知").strip(),
        }
        # 未知类别回退到训练集中出现最多的类别（LabelEncoder 无法直接 handle_unknown）
        cat_idx = []
        for f in self.CAT_FEATURES:
            classes = self._encoders[f].classes_
            hit = np.where(classes == cat_raw[f])[0]
            cat_idx.append(int(hit[0]) if len(hit) else int(np.argmax(classes == classes)))
        x_num = torch.tensor(self._scaler.transform(num), dtype=torch.float32)
        x_cat = torch.tensor([cat_idx], dtype=torch.long)

        self._model.eval()
        with torch.no_grad():
            pred_log = self._model(x_num, x_cat).item()
        pred = float(np.expm1(pred_log))

        interval = 1.5 * self._metrics.get("mae", pred * 0.1)
        pred = max(10.0, round(pred, 2))
        return {
            "status": "ok",
            "predicted_price": pred,
            "price_min": round(max(5.0, pred - interval), 2),
            "price_max": round(pred + interval, 2),
            "metrics": self._metrics,
        }

    def model_info(self) -> dict:
        """模型信息与评估指标"""
        metrics = self.ensure_trained()
        arch = None
        if self._model is not None:
            arch = {
                "type": "MLP 多层感知机（类别 Embedding + 全连接回归）",
                "embedding_dim": self.EMBED_DIM,
                "embedding_layers": [
                    f"{f}({self._cat_sizes[i]}类) -> Embedding({self.EMBED_DIM})"
                    for i, f in enumerate(self.CAT_FEATURES)
                ],
                "hidden_layers": " -> ".join(str(h) for h in self.HIDDEN) + " -> 1",
                "params": int(sum(p.numel() for p in self._model.parameters())),
            }
        return {
            "metrics": metrics if metrics.get("status") == "ok" else None,
            "algo": "深度学习 MLP 神经网络 (PyTorch)",
            "features": self.NUM_FEATURES + self.CAT_FEATURES,
            "arch": arch,
        }
