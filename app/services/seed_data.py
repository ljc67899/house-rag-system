# -*- coding: utf-8 -*-
"""数据库种子数据：示例房源、示例用户与管理员（仅空库时写入）"""

import os
import secrets

from datetime import datetime
from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_password
from app.models.house import HouseInfo
from app.models.user import UserInfo, Admin


def seed_sample_houses(db: Session):
    """空库时写入示例房源"""
    if db.query(HouseInfo).count() > 0:
        return
    samples = [
        {
            "title": "三里屯SOHO 精装两居 近地铁10号线",
            "rooms": "2室1厅1卫", "area": "82平米",
            "price": "8500元/月", "direction": "南北通透",
            "rent_type": "整租", "region": "朝阳区", "block": "三里屯街道",
            "address": "三里屯SOHO公寓", "traffic": "地铁10号线团结湖站步行300米",
            "facilities": "空调、洗衣机、冰箱、热水器、宽带、沙发、床、衣柜、电视",
            "highlights": "CBD核心地段，新装修，家具家电齐全，拎包入住，采光好",
            "matching": "三里屯太古里、工人体育场、超市、医院、学校",
            "contact_name": "张先生", "contact_phone": "138****1234",
            "longitude": "116.4551", "latitude": "39.9370",
            "image_url": "/static/images/houses/house_01_sanlitun.jpg",
        },
        {
            "title": "中关村软件园 华清嘉园 一居室",
            "rooms": "1室1厅1卫", "area": "48平米",
            "price": "5800元/月", "direction": "朝南",
            "rent_type": "整租", "region": "海淀区", "block": "中关村街道",
            "address": "华清嘉园", "traffic": "地铁13号线五道口站步行500米",
            "facilities": "空调、热水器、宽带、床、书桌、洗衣机",
            "highlights": "靠近清华北大，学区房，适合学生和IT从业者",
            "matching": "清华大学、北京大学、五道口商圈、书店、咖啡厅",
            "contact_name": "李女士", "contact_phone": "139****5678",
            "longitude": "116.3378", "latitude": "39.9932",
            "image_url": "/static/images/houses/house_02_huaqing.jpg",
        },
        {
            "title": "金融街 金宸公寓 豪华大三居",
            "rooms": "3室2厅2卫", "area": "125平米",
            "price": "18000元/月", "direction": "南北通透",
            "rent_type": "整租", "region": "西城区", "block": "金融街街道",
            "address": "金融街金宸公寓", "traffic": "地铁1/2号线复兴门站步行400米",
            "facilities": "全套家具家电、中央空调、智能家居、车位、储物间",
            "highlights": "金融街核心区，豪华装修，电梯高层，视野开阔，物业管理好",
            "matching": "金融街购物中心、各大银行总部、写字楼、高端餐饮",
            "contact_name": "王先生", "contact_phone": "136****9012",
            "longitude": "116.3603", "latitude": "39.9139",
            "image_url": "/static/images/houses/house_03_jinrongjie.jpg",
        },
        {
            "title": "国贸建外SOHO 时尚LOFT公寓",
            "rooms": "1室1厅1卫", "area": "65平米",
            "price": "7500元/月", "direction": "朝东",
            "rent_type": "整租", "region": "朝阳区", "block": "建外街道",
            "address": "建外SOHO", "traffic": "地铁1号线国贸站步行200米",
            "facilities": "智能家居、空调、洗衣机、冰箱、宽带、loft挑高",
            "highlights": "CBD核心，层高4.5米loft设计，时尚现代，年轻人首选",
            "matching": "国贸商圈、银泰中心、SKP、健身房、酒吧街",
            "contact_name": "赵女士", "contact_phone": "137****3456",
            "longitude": "116.4613", "latitude": "39.9087",
            "image_url": "/static/images/houses/house_04_jianwai_soho.jpg",
        },
        {
            "title": "回龙观龙泽苑 经济两居室",
            "rooms": "2室1厅1卫", "area": "78平米",
            "price": "4500元/月", "direction": "南北",
            "rent_type": "整租", "region": "昌平区", "block": "回龙观街道",
            "address": "龙泽苑小区", "traffic": "地铁13号线龙泽站步行600米",
            "facilities": "空调、洗衣机、热水器、床、衣柜、燃气灶",
            "highlights": "价格实惠，成熟社区，生活便利，适合上班族合租",
            "matching": "华联超市、城北市场、餐饮、小学、社区医院",
            "contact_name": "刘先生", "contact_phone": "135****7890",
            "longitude": "116.3412", "latitude": "40.0683",
            "image_url": "/static/images/houses/house_05_longzeyuan.jpg",
        },
        {
            "title": "望京SOHO 高档公寓一居室",
            "rooms": "1室1厅1卫", "area": "58平米",
            "price": "6800元/月", "direction": "朝南",
            "rent_type": "整租", "region": "朝阳区", "block": "望京街道",
            "address": "望京SOHO", "traffic": "地铁15号线望京站步行300米",
            "facilities": "全套家电、中央空调、宽带、健身房、泳池、24小时安保",
            "highlights": "望京核心地段，高档小区，物业服务好，配套设施齐全",
            "matching": "望京凯德MALL、阿里总部、奔驰大厦、餐饮、电影院",
            "contact_name": "陈女士", "contact_phone": "134****2345",
            "longitude": "116.4772", "latitude": "39.9936",
            "image_url": "/static/images/houses/house_06_wangjing_soho.jpg",
        },
        {
            "title": "学院路 清枫华景园 三居合租",
            "rooms": "3室1厅1卫", "area": "95平米",
            "price": "7200元/月", "direction": "南北",
            "rent_type": "整租", "region": "海淀区", "block": "学院路街道",
            "address": "清枫华景园", "traffic": "地铁15号线六道口站步行500米",
            "facilities": "空调、洗衣机、冰箱、热水器、基本家具、宽带",
            "highlights": "学院路核心区，靠近多所高校，适合学生合租",
            "matching": "北京林业大学、中国农大、超市、餐饮、书店",
            "contact_name": "孙先生", "contact_phone": "133****6789",
            "longitude": "116.3513", "latitude": "40.0032",
            "image_url": "/static/images/houses/house_07_qingfenghuajing.jpg",
        },
        {
            "title": "亦庄金茂悦 新小区精装两居",
            "rooms": "2室2厅1卫", "area": "88平米",
            "price": "5500元/月", "direction": "南北通透",
            "rent_type": "整租", "region": "大兴区", "block": "亦庄地区",
            "address": "亦庄金茂悦", "traffic": "地铁亦庄线荣昌东街站步行700米",
            "facilities": "全新家具家电、中央空调、地暖、新风系统、车位",
            "highlights": "新房首次出租，科技住宅，小区环境优美，人车分流",
            "matching": "亦庄创意生活广场、沃尔玛、北京二中亦庄学校、南海子公园",
            "contact_name": "周女士", "contact_phone": "132****0123",
            "longitude": "116.5042", "latitude": "39.7928",
            "image_url": "/static/images/houses/house_08_yizhuang_jinmaoyue.jpg",
        },
        {
            "title": "五道口 华清嘉园 单身公寓",
            "rooms": "1室0厅1卫", "area": "32平米",
            "price": "4200元/月", "direction": "朝南",
            "rent_type": "整租", "region": "海淀区", "block": "中关村街道",
            "address": "华清嘉园", "traffic": "地铁13号线五道口站步行300米",
            "facilities": "空调、热水器、宽带、单人床、书桌、衣柜",
            "highlights": "宇宙中心位置，靠近高校，适合考研和实习学生",
            "matching": "清华北大、五道口美食街、书店、咖啡厅、超市",
            "contact_name": "吴先生", "contact_phone": "131****4567",
            "longitude": "116.3378", "latitude": "39.9932",
            "image_url": "/static/images/houses/house_09_wudaokou_studio.jpg",
        },
        {
            "title": "亚运村 汇欣公寓 家庭四居",
            "rooms": "4室2厅2卫", "area": "165平米",
            "price": "15000元/月", "direction": "南北通透",
            "rent_type": "整租", "region": "朝阳区", "block": "亚运村街道",
            "address": "汇欣公寓", "traffic": "地铁8号线奥体中心站步行500米",
            "facilities": "豪华装修、全套家电、中央空调、车位、储物间、健身器材",
            "highlights": "亚奥核心区，大户型，适合家庭居住，小区环境优美",
            "matching": "鸟巢水立方、亚运村商圈、安贞医院、朝阳外国语学校",
            "contact_name": "郑先生", "contact_phone": "130****8901",
            "longitude": "116.3968", "latitude": "39.9923",
            "image_url": "/static/images/houses/house_10_yayuncun.jpg",
        },
        {
            "title": "通州万达 精装两居 近地铁",
            "rooms": "2室1厅1卫", "area": "75平米",
            "price": "4800元/月", "direction": "南北",
            "rent_type": "整租", "region": "通州区", "block": "新华街道",
            "address": "万达广场公寓", "traffic": "地铁八通线通州北苑站步行400米",
            "facilities": "空调、洗衣机、冰箱、热水器、宽带、家具齐全",
            "highlights": "通州核心区，下楼就是万达，生活便利，近地铁",
            "matching": "通州万达广场、家乐福、餐饮、电影院、通州医院",
            "contact_name": "冯先生", "contact_phone": "138****2233",
            "longitude": "116.6573", "latitude": "39.9092",
            "image_url": "/static/images/houses/house_11_tongzhou_wanda.jpg",
        },
        {
            "title": "丰台丽泽 高端公寓两居室",
            "rooms": "2室2厅1卫", "area": "90平米",
            "price": "7800元/月", "direction": "南北通透",
            "rent_type": "整租", "region": "丰台区", "block": "卢沟桥街道",
            "address": "丽泽景园", "traffic": "地铁14号线丽泽商务区站步行600米",
            "facilities": "精装修、全套家电、中央空调、新风、车位",
            "highlights": "丽泽金融商务区，高端社区，升值潜力大",
            "matching": "丽泽天街、丰台科技园、307医院、餐饮购物",
            "contact_name": "许女士", "contact_phone": "139****4455",
            "longitude": "116.3215", "latitude": "39.8623",
            "image_url": "/static/images/houses/house_12_lize_jingyuan.jpg",
        },
    ]
    now = datetime.now()
    for s in samples:
        db.add(HouseInfo(**s, publish_date=now.strftime("%Y-%m-%d")))
    db.commit()
    logger.info(f"Seeded {len(samples)} sample houses")


def seed_sample_users(db: Session):
    """空库时写入示例用户与管理员"""
    if db.query(UserInfo).count() > 0 and db.query(Admin).count() > 0:
        return
    now = datetime.now()
    # 示例用户口令同样不写死：仓库公开等于公开账号。
    # 优先取 DEMO_USER_PASSWORD，未配置则随机生成并打印一次。
    demo_pwd = (os.getenv("DEMO_USER_PASSWORD") or "").strip() or secrets.token_urlsafe(9)
    users = [
        {"name": "admin", "password": demo_pwd, "email": "admin@example.com", "addr": "北京市朝阳区"},
        {"name": "testuser", "password": demo_pwd, "email": "test@example.com", "addr": "北京市海淀区"},
    ]
    for u in users:
        db.add(UserInfo(**u, create_time=now))
    logger.warning(f"已创建示例用户 admin / testuser，口令: {demo_pwd}（仅供演示）")
    if db.query(Admin).count() == 0:
        # 口令不写死（仓库公开时等于公开后台口令）：优先取 ADMIN_INIT_PASSWORD，
        # 否则随机生成并打印到日志，由部署者自己保存。
        pwd = (settings.admin_init_password or "").strip() or secrets.token_urlsafe(12)
        db.add(Admin(username="admin", password_hash=hash_password("admin", pwd), create_time=now))
        logger.warning(f"已创建管理员 admin，初始口令: {pwd}（请登录后立即修改）")
    db.commit()
    logger.info(f"Seeded {len(users)} sample users")
