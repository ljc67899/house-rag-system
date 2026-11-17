# -*- coding: utf-8 -*-
"""应用配置管理"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "二手房智能问答推荐系统"
    app_version: str = "2.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8001
    # 以下两项的默认值只是占位符，真实值必须写在 .env（.env 不进版本库）。
    # 仓库要公开，所以这里不能出现任何真实口令/密钥。
    secret_key: str = "change_me_in_dotenv"

    database_url: str = "mysql+pymysql://root:your_password@127.0.0.1:3306/house_rag_system?charset=utf8mb4"

    llm_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model_name: str = "qwen-plus"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048
    # 可选：多模态模型（如 qwen-vl-plus），配置后「看房报告」会额外分析房源实拍图
    llm_vision_model: str = ""

    embedding_type: str = "api"
    embedding_api_model: str = "text-embedding-v2"
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32

    vector_db_type: str = "chroma"
    vector_db_path: str = "./data/vector_db"
    vector_db_collection: str = "house_rag_knowledge"

    document_storage_path: str = "./data/documents"
    max_upload_size: int = 50 * 1024 * 1024
    allowed_extensions: str = ".pdf,.docx,.txt,.md"

    rag_top_k: int = 5
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 50
    rag_score_threshold: float = 0.5

    # 房源向量库的索引条数上限（按「小区 × 户型」分层抽样）
    # 数据库里有 38266 条房源、4900 个小区、13355 个「小区 × 户型」组合。
    # 早先这里写死 1000 且按"每区取 id 前 N 条"，结果：
    #   1) 向量库只覆盖 288 个小区，一个小区平均 3 套，检索几乎没有竞争，
    #      评测里 recall@k 全部饱和到 1.000，分数漂亮却测不出排序能力；
    #   2) 扩到 4000 后反而有小区整片消失（安苑里 / 双龙南里 / 安慧里三区），
    #      因为房源按街道成团落库，某个小区可能全部落在配额之外。
    # 现在改成按「小区 × 户型」保底各 1 条（13355 条），余量按 id 补齐到 16000。
    # 这样覆盖完整、评测集不会因扩缩量失效，检索也终于有了竞争。
    #
    # 为什么又放到全量（>= 库内总数即全量）：
    #   16000 只保住了「每个小区×户型组合有 1 条」，余量按 id 顺序补，于是 id 靠前的
    #   145 个小区整片入库（77/77），靠后的 1500 个小区每个户型只留 1 套——库里
    #   38266 条有 22266 条（58.2%）永远检索不到。用户问「双龙南里 两居」只能给 1 套
    #   （实际 5 套）、「太月园 两居」只给 2 套（实际 17 套），「同小区多套对比」直接没了，
    #   而且这是语料缺失，任何检索器/精排都救不回来。
    #   bge-small 嵌全量不成问题（16000 条本来就跑得动），HNSW 查询是对数级，
    #   检索时延基本不变。留 40000 的余量，避免新增房源后又被抽样切掉。
    rag_house_index_limit: int = 40000

    # ===== 检索链路参数：召回 → 融合 → 精排 → 多样性 =====
    # 候选池必须显著大于最终取数（top_k），名次类融合（RRF）才有区分度
    rag_dense_recall_k: int = 50
    rag_sparse_recall_k: int = 50
    rag_rrf_k: int = 60          # RRF 常数，原论文默认值
    rag_fuse_top_n: int = 20     # 融合后进入精排的候选数
    # 加权 RRF：字面型查询（含小区名/地铁）让稀疏路权重更高，
    # 否则精确匹配的第一名会被稠密路的第 5 名「投票」稀释掉
    rag_rrf_w_dense: float = 1.0
    rag_rrf_w_sparse: float = 1.0
    rag_rrf_w_dense_literal: float = 1.0
    rag_rrf_w_sparse_literal: float = 1.6
    # cross-encoder 精排：对「query-doc」整体打相关性分，不是比相似度
    rag_rerank_enabled: bool = True
    rag_rerank_model: str = "BAAI/bge-reranker-base"
    # 多样性：同一小区只保留最高分一条优先入选，避免 top5 全是同一个小区
    rag_diversity: bool = True
    # 上下文预算（token），超出按优先级裁剪
    rag_context_token_budget: int = 3000

    # ===== 外部地理数据（高德 Web 服务）=====
    # 房源库只覆盖北京；外地问题用高德补充「行政区 / 商圈 / 地铁」这类地理事实，
    # 只补地理信息，不抓第三方房源，避免法律与稳定性风险。
    amap_web_key: str = ""
    external_data_timeout: float = 4.0
    external_data_cache_ttl: int = 86400  # 同城同区一天内不重复计费

    # 高德「Web端(JS API)」的 Key 与安全密钥。
    # 以前这两个值直接写死在前端 index.html / admin.js 里，随仓库公开就等于
    # 把配额白送人（高德 Key 谁都能拿去刷）。现在改由后端 /api/config/map 下发，
    # 源码里不再出现；注意这只是「源码不含密钥」，浏览器端仍然可见，
    # 真正的防线是在高德控制台给它绑域名白名单 + 配每日调用上限。
    amap_js_key: str = ""
    amap_js_security_code: str = ""

    # 首次启动创建管理员的初始口令。留空则随机生成 16 位并打印到启动日志，
    # 避免仓库里出现 admin/admin123 这种「公开的后门」。
    admin_init_password: str = ""

    hf_mirror: str = ""

    @property
    def allowed_extensions_list(self) -> list:
        return [ext.strip() for ext in self.allowed_extensions.split(",") if ext.strip()]

    @property
    def allowed_extensions_set(self) -> set:
        return {ext.lower() for ext in self.allowed_extensions_list}


settings = Settings()
