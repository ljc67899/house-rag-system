# LLM API Key 配置指南

本项目使用 OpenAI 兼容格式的 API，可以接入多种大模型服务。以下是几个**免费**的方案推荐。

---

## 方案一：硅基流动（最推荐，免费额度多）

**免费额度：注册即送 2000 万 Token，9B 以下小模型永久免费**

官网：https://cloud.siliconflow.cn/

### 配置步骤：

1. 打开硅基流动官网注册账号
2. 进入「API 密钥」页面创建新的 API Key
3. 修改项目根目录下的 .env 文件：

LLM_API_BASE=https://api.siliconflow.cn/v1
LLM_API_KEY=你的APIKey
LLM_MODEL_NAME=deepseek-ai/DeepSeek-V3

### 推荐模型：
- deepseek-ai/DeepSeek-V3 - 深度求索最新模型，能力强
- Qwen/Qwen2.5-7B-Instruct - 通义千问，永久免费
- deepseek-ai/DeepSeek-R1 - 推理模型，适合复杂问题

---

## 方案二：智谱 AI（国产自研，GLM 系列）

**免费额度：新用户送 2000 万 Tokens**

官网：https://open.bigmodel.cn/

### 配置步骤：

1. 打开智谱 AI 开放平台注册
2. 进入控制台获取 API Key
3. 修改 .env 文件：

LLM_API_BASE=https://open.bigmodel.cn/api/paas/v4
LLM_API_KEY=你的APIKey
LLM_MODEL_NAME=glm-4-flash

### 推荐模型：
- glm-4-flash - 轻量高速版，免费额度内足够使用
- glm-4-plus - 能力更强

---

## 方案三：阿里云 DashScope（通义千问）

**免费额度：新用户有一定免费额度，学生可申请更多**

官网：https://dashscope.console.aliyun.com/

### 配置步骤：

1. 打开阿里云 DashScope 控制台注册
2. 获取 API-KEY
3. 修改 .env 文件（默认已是这个配置，只需填 Key）：

LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=你的APIKey
LLM_MODEL_NAME=qwen-plus

### 推荐模型：
- qwen-turbo - 快速版，价格便宜
- qwen-plus - 平衡版（默认）
- qwen-max - 最强能力版

---

## 配置方法

### 方式一：修改 .env 文件（推荐）

直接编辑项目根目录下的 .env 文件，填入对应的配置即可。

### 方式二：环境变量

也可以通过设置系统环境变量来配置：
- LLM_API_BASE - API 地址
- LLM_API_KEY - API 密钥
- LLM_MODEL_NAME - 模型名称

---

## 验证配置

配置完成后，重启服务，打开 AI 问答页面，输入问题测试：
- 如果能正常回答，说明配置成功
- 如果提示"请检查 LLM API Key 配置"，说明 Key 未配置或有误

---

## 小贴士

1. 个人学习/小规模试用：推荐硅基流动，2000万 Token 足够用很久
2. 所有平台都兼容 OpenAI 格式，只需更换 API 地址和 Key 即可
3. 注意保护好你的 API Key，不要提交到公开代码仓库
4. 部分平台需要实名认证才能使用 API 功能
