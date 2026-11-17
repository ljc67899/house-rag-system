# 租房数据集说明

## 📁 数据集存放位置

```
house-rag-system/data/datasets/
```

## 📊 已有的真实数据集

### 1. MySQL 真实房源数据
- **数据来源**：MySQL `house` 数据库 `house_info` 表（链家/我爱我家真实房源）
- **数据量**：126 套真实房源
- **文件位置**：`mysql_house_data.csv`
- **字段说明**：
  - id: 房源ID
  - title: 房源标题
  - rooms: 户型（如 2室1厅1卫）
  - area: 面积（如 84平米）
  - price: 价格（如 4200元/月）
  - direction: 朝向
  - rent_type: 出租方式（整租/合租）
  - region: 区域
  - block: 商圈
  - address: 小区
  - traffic: 交通
  - facilities: 配套设施
  - highlights: 房源亮点
  - matching: 周边配套
  - landlord: 房东
  - phone: 联系电话
  - house_num: 房源编号
  - page_views: 浏览量
  - publish_time: 发布时间

### 2. 链家网爬虫数据
- **数据来源**：链家网公开房源页面
- **文件位置**：`lianjia_beijing_zufang.csv`、`lianjia_mobile_beijing.csv`
- **说明**：从链家首页爬取的真实房源数据（受反爬限制数量较少）

## 🌐 推荐公开数据集下载平台

### 1. 明尼苏达大学 DRUM（推荐，学术级）
- **网址**：https://conservancy.umn.edu/items/29cab34a-f4d7-4ba0-ac9e-bd1bbe7d371e
- **数据集名**：Private Rental Listings in Beijing, 2015 and 2018
- **数据来源**：链家网 + 我爱我家
- **数据量**：数万条（2015+2018年）
- **许可**：CC0 1.0 公有领域，可免费使用
- **格式**：TXT / XLSX

### 2. 和鲸社区 HeyWhale（国内平台）
- **网址**：https://www.heywhale.com/home/dataset
- **搜索关键词**："链家租房"、"北京租房"
- **特点**：国内平台，访问速度快，免费注册后可下载

### 3. 阿里天池
- **网址**：https://tianchi.aliyun.com/dataset
- **搜索关键词**："租房"、"链家"、"租金预测"
- **特点**：阿里云旗下，数据质量高，部分需注册

### 4. Kaggle（国际平台）
- **网址**：https://www.kaggle.com/datasets
- **搜索关键词**："house rent china"、"beijing rent"、"lianjia"
- **特点**：全球最大数据科学平台，数据集丰富

### 5. 百度网盘分享
- **链家北京租房数据（3000余条）**：
  - 链接：https://pan.baidu.com/s/1EqfsnGlb63HeUlKlQfGEgg
  - 提取码：i20b
  - 来源：CSDN博主分享，链家网爬取

### 6. GitHub 开源项目
- Easter1995/Lianjia-Data-Analyze：北上广深大理租房数据
- yifi99/lianjia-spider：21城链家爬虫
- if-always/Zu_Fang：北上广深租房数据

## 🔧 数据导入方式

### 方式一：CSV 导入（推荐）
1. 将下载的 CSV 文件放入 `data/datasets/` 目录
2. 运行导入脚本即可自动导入系统

### 方式二：MySQL 直连
1. 确保 MySQL 服务已启动
2. 修改 `.env` 文件：
   ```
   DATABASE_URL=mysql+pymysql://root:123456@localhost:3306/house_rag_system?charset=utf8mb4
   ```
3. 重启服务，系统自动创建表结构

### 方式三：从现有 MySQL 导入
1. 运行数据迁移脚本
2. 从 `house` 数据库导入到 `house_rag_system`

## 🗄️ MySQL 配置信息

- **主机**：localhost
- **端口**：3306
- **用户名**：root
- **密码**：123456
- **系统数据库**：house_rag_system
- **已有房产库**：house（含126条真实房源）

## 📝 自行采集数据

系统提供爬虫脚本，可自行采集最新数据：
- 脚本：`lianjia_zufang_crawler.py`
- 来源：链家网公开房源信息
- 注意：请遵守网站 robots 协议，控制访问频率
