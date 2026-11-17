/* =========================================
 * 前台业务逻辑（Vue 3 CDN，无构建，单 createApp + setup）
 *
 * 前端文件分工：
 *   index.html     页面模板 + SVG 图标精灵（Vue 模板必须与挂载点同文档）
 *   js/api.js      请求层：token 注入、get / post / put / del / upload
 *   js/main.js     本文件：全部视图状态与业务逻辑
 *   css/*.css      按页面职责拆分（base/home/find/chat/auth/detail/
 *                  pages/dashboard/modules/theme/home-v2），加载顺序见 index.html
 *   admin/         独立管理后台，不依赖本文件与前台样式
 *
 * 本文件区块顺序：
 *   1. 全局状态（tab / 搜索 / 列表 / 地图 / 详情）
 *   2. 知识库管理（管理员）
 *   3. 登录 Token 与 RBAC 权限
 *   4. 首页：热门房源、区域卡片、概览统计
 *   5. 找房：列表筛选、分页、地图打点
 *   6. AI 问答：会话、流式回答、知识库引用
 *   7. 详情页：评价、周边配套、口碑摘要
 *   8. 推荐 / 收藏 / 对比 / 浏览历史
 *   9. 价格分析与数据大屏（模块 A~E）
 *  10. 登录 / 注册 / 改密 / 弹窗控制
 *
 * 注意：房源增删改查已迁至独立后台（/admin），本文件不再包含管理逻辑。
 * ========================================= */
const { createApp, ref, computed, watch, onMounted, nextTick } = Vue;

    createApp({
      setup() {
        const tab = ref('home');
        // 首页 hero 搜索框的关键词（同时作为列表查询条件）
        const searchKw = ref('');
        // 顶部导航搜索框独立管理，避免和首页 hero 输入框双向同步
        const headerKw = ref('');
        const houses = ref([]);
        const hotHouses = ref([]);
        const recHouses = ref([]);
        // 首页「按区域找房」：区域 + 房源数（/houses/regions 返回的全量，取前 8）
        const homeRegions = ref([]);
        const mapHouses = ref([]);
        const mapRegion = ref('');
        const mapLoaded = ref(false);
        const mapHousesTotal = ref(0);
        const mapPage = ref(1);
        const mapPageSize = ref(20);
        const mapHousesAll = ref([]);
        const detailHouse = ref(null);
        const favHouses = ref([]);
        const compareHouses = ref([]);
        const historyList = ref([]);
        const filter = ref({ region: '', rooms: '', priceRange: '' });
        const housePage = ref(1);
        const housePageSize = ref(24);
        const totalHouses = ref(0);
        // 页码跳转输入框（1595 页时靠点"下一页"翻页不现实）
        const pageJump = ref('');
        const houseTotalPages = computed(
          () => Math.ceil((totalHouses.value || 0) / (housePageSize.value || 24)) || 1);
        // AI 问答：每条回答各自保存「本轮检索到且带经纬度的房源」（挂在 message 上，
        // 历史对话的地图才能保留；切 tab 重建 DOM 后靠它重新挂载）
        const CHAT_WELCOME = `您好！我是安居乐房产智能助手
有什么买房问题都可以问我，比如：
• 朝阳区有哪些两居室房源？
• 300万以下能买到什么样的房子？
• 买房有哪些注意事项？`;
        // 多轮记忆的会话 ID。
        // 后端按 conversation_id 存上下文（LangGraph MemorySaver + thread_id），
        // 前端不传的话后端每次都 uuid4() 新建线程 —— 这就是「AI 记不住上一句」的根因：
        // 记忆做了，但没接通。存 sessionStorage：同标签页刷新仍连续，关掉即新会话；
        // 退出登录时必须清掉，否则换账号会继承上一个人的对话上下文。
        const CHAT_CID_KEY = 'chat_cid';
        const messages = ref([{ role: 'ai', content: CHAT_WELCOME }]);
        const chatCid = ref('');
        const chatInput = ref('');
        const chatLoading = ref(false);
        const estForm = ref({ region: '朝阳区', area: 80, rooms: '2室1厅', direction: '南北', floor: '中楼层', decoration: '精装' });
        const estData = ref(null);
        const estLoading = ref(false);
        // 模块 A：机器学习预测
        const mlData = ref(null);
        const mlError = ref('');
        // 模块 B：协同过滤推荐
        const userProfile = ref(null);
        const recStrategy = ref('');
        // 首页只展示 4 条精选（一行，与热门房源的 4 列网格对齐），完整列表 + 画像 + 策略 + 匹配度留在「AI推荐」页，
        // 否则两页内容完全一样，推荐页就没有存在意义了
        const homeRecHouses = computed(() => (recHouses.value || []).slice(0, 4));
        // 模块 C：数据大屏
        const dashData = ref(null);
        const dashLoading = ref(false);
        // 模块 D：评论情感分析已并入房源详情页（口碑摘要），无独立页面状态
        // 模块 E：走势分析
        const trendRegions = ref([]);
        const trendRegion = ref('');
        const trendData = ref(null);
        const trendOverview = ref(null);
        // 走势页：所选区域的板块截面行情（单区深挖，区别于数据分析页的区域横向对比）
        const trendBlocks = ref(null);
        // 板块 / 热门小区 两种口径：block 字段覆盖不足的区域自动切到小区
        const trendBlockMode = ref('block');
        const trendBlockList = computed(() => {
          const d = trendBlocks.value;
          if (!d) return [];
          return trendBlockMode.value === 'community'
            ? (d.communities || [])
            : (d.blocks || []);
        });
        // 偏离最大值的绝对值，用来把条形按比例缩放（避免某个高价盘把其他条压扁）
        const trendBlockMaxAbs = computed(() => {
          const list = trendBlockList.value;
          if (!list.length) return 1;
          return Math.max(1, ...list.map(b => Math.abs(b.vs_region_pct || 0)));
        });
        const trendError = ref('');
        const chartInstances = {};

        // 用户登录状态
        const currentUser = ref(null);
        const showUserMenu = ref(false);
        const mobileMenuOpen = ref(false);
        const showLoginModal = ref(false);
        const showRegisterModal = ref(false);
        const loginForm = ref({ username: '', password: '' });
        const rememberPassword = ref(false);

        /** Base64 编解码：只做本地弱混淆，不是加密，请勿在公共设备勾选记住密码 */
        function encodeB64(text) {
          try { return btoa(unescape(encodeURIComponent(text || ''))); } catch (e) { return ''; }
        }
        function decodeB64(text) {
          try { return decodeURIComponent(escape(atob(text || ''))); } catch (e) { return ''; }
        }

        // 页面加载时恢复「记住密码」保存的账号密码（兼容旧的 remember_username）
        try {
          const savedLogin = localStorage.getItem('remember_login');
          if (savedLogin) {
            const obj = JSON.parse(savedLogin);
            if (obj && obj.u) {
              loginForm.value.username = obj.u;
              loginForm.value.password = obj.p ? decodeB64(obj.p) : '';
              rememberPassword.value = true;
            }
          } else {
            const saved = localStorage.getItem('remember_username');
            if (saved && saved !== 'undefined' && saved !== 'null') {
              loginForm.value.username = saved;
            }
          }
        } catch(e) {}

        /** 取消勾选「记住密码」时立即清除本地保存 */
        function onRememberPasswordChange() {
          if (!rememberPassword.value) {
            try { localStorage.removeItem('remember_login'); } catch (e) {}
          }
        }
        const loginLoading = ref(false);
        const loginError = ref('');
        const registerForm = ref({ username: '', email: '', password: '', confirmPassword: '' });
        const registerLoading = ref(false);
        const registerError = ref('');
        const chatBodyRef = ref(null);

        // 一级导航按“用户任务”组织：找房（列表/地图）、房价（估价/走势）各自合并为一页，
        // 收藏/对比/足迹收进头像菜单，避免一级入口过多。
        const navItems = [
          { key: 'home', label: '首页' },
          { key: 'find', label: '找房' },
          { key: 'recommend', label: '智能推荐' },
          { key: 'chat', label: 'AI问答' },
          { key: 'price', label: '房价' },
          { key: 'dashboard', label: '数据分析' },
          // 后台已独立成 /admin 站点（app/static/admin/），前台只保留一个跳转入口
          { key: 'admin', label: '管理后台', external: '/admin' },
        ];
        const activeDropdown = ref(null);

        // 「找房」页内视图：列表 / 地图
        const findView = ref('list');
        // 「房价」页内视图：估价 / 走势
        const priceView = ref('estimate');
        // 「AI问答」页内视图：智能问答 / 知识库（知识库不再单独占一级入口）
        const chatView = ref('chat');

        // 区域列表由 /houses/regions 动态加载，避免数据里有的区在下拉框里选不到
        const regions = ref(['朝阳区', '海淀区', '西城区', '东城区', '丰台区', '昌平区', '大兴区', '通州区']);

        // 各区中心坐标：切换地区时直接把地图定位到区中心（比按散点包围盒更稳定）
        const MAP_REGION_CENTERS = {
          '东城区': [116.4188, 39.9175],
          '西城区': [116.3660, 39.9123],
          '朝阳区': [116.4864, 39.9219],
          '丰台区': [116.2867, 39.8585],
          '石景山区': [116.2228, 39.9066],
          '海淀区': [116.2981, 39.9593],
          '门头沟区': [116.1020, 39.9404],
          '房山区': [116.1433, 39.7491],
          '通州区': [116.6571, 39.9099],
          '顺义区': [116.6544, 40.1302],
          '昌平区': [116.2312, 40.2207],
          '大兴区': [116.3380, 39.7289],
          '怀柔区': [116.6371, 40.3243],
          '平谷区': [117.1123, 40.1448],
          '密云区': [116.8433, 40.3770],
          '延庆区': [115.9750, 40.4567],
          '北京': [116.4074, 39.9042],
        };

        const suggestions = [
          '有哪些两居室房源？',
          '300万以下的房子',
          '朝阳区房源推荐',
          '买房注意事项',
        ];

        const knowledgeCategories = ['购房指南', '区域分析', '交易流程', '贷款知识', '税费计算', '装修指南', '避坑指南'];
        const knowledgeCat = ref('');
        const showKnowledgeDetail = ref(false);
        const currentKnowledge = ref({});
        
        const knowledgeDocs = [
          { 
            id: 1, icon: 'file-text', title: '二手房购房合同注意事项', 
            summary: '签订二手房合同是买房最重要的环节，详细介绍合同条款审查要点、常见陷阱和防范措施。',
            category: '购房指南', tags: ['合同', '注意事项', '避坑'], readTime: 8, time: '2026-08-10',
            fullContent: `<h4>一、确认业主身份</h4>
            <p>签订合同前务必核实业主身份，要求出示身份证、房产证原件，确保证书与身份证一致。如有代理人，需查验公证委托书。</p>
            <h4>二、房屋产权审查</h4>
            <p>1. 查看房产证原件，确认产权性质（商品房/公房/经济适用房）<br>
            2. 核实房屋是否有抵押、查封等权利限制<br>
            3. 确认是否为满五唯一（影响税费）<br>
            4. 公房需确认是否已补交土地出让金</p>
            <h4>三、合同核心条款</h4>
            <p><strong>1. 成交价格：</strong>明确总价、付款方式、付款时间节点<br>
            <strong>2. 交房时间：</strong>明确交房日期、交房条件、逾期违约责任<br>
            <strong>3. 户口迁出：</strong>约定户口迁出时间和违约金<br>
            <strong>4. 税费承担：</strong>明确各项税费由哪方承担<br>
            <strong>5. 违约责任：</strong>明确双方违约情形及赔偿标准</p>
            <h4>四、常见陷阱</h4>
            <p>• 阴阳合同：为避税签订低价合同，风险极大<br>
            • 定金陷阱：定金与订金的区别，定金不超过总价20%<br>
            • 共有房产：夫妻共有需双方签字<br>
            • 租户优先购买权：有租约的房屋需租户放弃优先购买权</p>`
          },
          { 
            id: 2, icon: 'map', title: '北京买房区域全攻略', 
            summary: '北京各区域买房特点深度解析：朝阳、海淀、西城、东城、丰台、通州...帮你找到最适合的区域。',
            category: '区域分析', tags: ['北京', '区域', '选房'], readTime: 12, time: '2026-08-12',
            fullContent: `<h4>朝阳区</h4>
            <p><strong>特点：</strong>商务氛围浓，高端楼盘多，国际化程度高<br>
            <strong>均价：</strong>约7-12万/㎡<br>
            <strong>适合人群：</strong>国贸/CBD上班族、追求品质生活的改善型买家<br>
            <strong>热门板块：</strong>望京、双井、百子湾、朝青、亚运村</p>
            <h4>海淀区</h4>
            <p><strong>特点：</strong>学区资源顶尖，IT互联网聚集，文化氛围浓<br>
            <strong>均价：</strong>约8-15万/㎡<br>
            <strong>适合人群：</strong>重视教育的家庭、互联网从业者<br>
            <strong>热门板块：</strong>中关村、五道口、万柳、世纪城、西二旗</p>
            <h4>西城区</h4>
            <p><strong>特点：</strong>金融街核心区，教育资源顶级，政要聚集<br>
            <strong>均价：</strong>约10-18万/㎡<br>
            <strong>适合人群：</strong>金融从业者、顶级学区需求家庭<br>
            <strong>热门板块：</strong>金融街、德胜、月坛、陶然亭</p>
            <h4>东城区</h4>
            <p><strong>特点：</strong>老北京底蕴，教育资源优质，交通便利<br>
            <strong>均价：</strong>约9-16万/㎡<br>
            <strong>适合人群：</strong>重视教育的家庭、喜欢老城区的买家<br>
            <strong>热门板块：</strong>东直门、和平里、崇文门、方庄</p>
            <h4>通州区</h4>
            <p><strong>特点：</strong>城市副中心，规划利好，价格洼地，潜力大<br>
            <strong>均价：</strong>约4-6万/㎡<br>
            <strong>适合人群：</strong>刚需上车、投资看好副中心发展<br>
            <strong>热门板块：</strong>运河核心区、武夷花园、台湖</p>`
          },
          { 
            id: 3, icon: 'refresh', title: '二手房交易完整流程', 
            summary: '从选房看房、签合同、办贷款到过户交房，一文讲清二手房交易全流程的每一步和注意事项。',
            category: '交易流程', tags: ['流程', '过户', '交房'], readTime: 10, time: '2026-08-15',
            fullContent: `<h4>第一步：选房看房（1-4周）</h4>
            <p>1. 明确需求：预算、区域、户型、面积<br>
            2. 线上筛选：APP、网站浏览房源<br>
            3. 实地看房：至少看10-20套，注意采光、噪音、楼层<br>
            4. 产权核实：房产证、业主身份、有无抵押</p>
            <h4>第二步：谈价签约（1-3天）</h4>
            <p>1. 价格谈判：了解市场价，合理出价<br>
            2. 交定金：一般10-50万，不超过总价20%<br>
            3. 签合同：中介居间合同，明确所有条款<br>
            4. 资质审核：购房资格、贷款资质预审</p>
            <h4>第三步：办理贷款（2-4周）</h4>
            <p>1. 评估：银行指定评估公司评估房屋价值<br>
            2. 面签：买卖双方到银行签贷款合同<br>
            3. 批贷：银行审批，出具批贷函<br>
            4. 公积金贷款：单独办理，周期稍长</p>
            <h4>第四步：缴税过户（1-2周）</h4>
            <p>1. 网签：住建委网上签约备案<br>
            2. 缴税：契税、个税、增值税等<br>
            3. 过户：不动产登记中心办理过户<br>
            4. 拿证：买方拿到不动产权证</p>
            <h4>第五步：交房收尾（1-2周）</h4>
            <p>1. 物业交割：水、电、气、暖、物业费结清<br>
            2. 户口迁出：确认原业主户口已迁出<br>
            3. 钥匙交接：验房后交接钥匙<br>
            4. 尾款支付：确认无问题后支付尾款</p>`
          },
          { 
            id: 4, icon: 'yuan', title: '房贷知识全解析', 
            summary: '商业贷款 vs 公积金贷款 vs 组合贷款，首付比例、利率计算、还款方式选择全攻略。',
            category: '贷款知识', tags: ['贷款', '首付', '利率'], readTime: 9, time: '2026-08-14',
            fullContent: `<h4>一、贷款类型</h4>
            <p><strong>商业贷款：</strong>银行发放，额度高，利率较高，适合所有人<br>
            <strong>公积金贷款：</strong>利率低（3.1%左右），额度有限，需连续缴存<br>
            <strong>组合贷款：</strong>公积金+商贷结合，兼顾低利率和高额度</p>
            <h4>二、首付比例</h4>
            <p>首套房：首付 30-40%<br>
            二套房：首付 50-80%（各区政策不同）<br>
            非普通住宅：首付比例更高</p>
            <h4>三、利率计算</h4>
            <p>当前参考利率：<br>
            • 首套商贷：LPR + 55个基点（约4.2%）<br>
            • 二套商贷：LPR + 105个基点（约4.7%）<br>
            • 公积金：首套3.1%，二套3.575%</p>
            <h4>四、还款方式</h4>
            <p><strong>等额本息：</strong>每月还款额相同，前期利息多，适合收入稳定者<br>
            <strong>等额本金：</strong>每月递减，总利息少，适合前期还款能力强者</p>
            <h4>五、贷款建议</h4>
            <p>1. 尽量使用公积金贷款，利率低很多<br>
            2. 贷款年限尽量长（30年），抵御通胀<br>
            3. 月供不超过家庭月收入的50%<br>
            4. 预留6个月月供作为应急资金</p>`
          },
          { 
            id: 5, icon: 'calculator', title: '二手房税费计算指南', 
            summary: '契税、个税、增值税、中介费...买一套房到底要交多少税？本文帮你算清楚。',
            category: '税费计算', tags: ['税费', '契税', '个税'], readTime: 7, time: '2026-08-13',
            fullContent: `<h4>主要税费一览</h4>
            <p><strong>1. 契税（买方交）</strong><br>
            首套90㎡以下：1%<br>
            首套90㎡以上：1.5%<br>
            二套：3%<br>
            非普通住宅：3%</p>
            <p><strong>2. 个人所得税（卖方交）</strong><br>
            满五唯一：免征<br>
            不满五唯一：差额20% 或 全额1%</p>
            <p><strong>3. 增值税及附加（卖方交）</strong><br>
            满2年普通住宅：免征<br>
            不满2年：全额5.3%<br>
            满2年非普通：差额5.3%</p>
            <p><strong>4. 中介费</strong><br>
            一般为总房价的1-3%，可议价</p>
            <h4>举例计算</h4>
            <p>以500万、80㎡、满五唯一、首套房为例：<br>
            契税：500万 × 1% = 5万<br>
            个税：免征（满五唯一）<br>
            增值税：免征（满2年）<br>
            中介费：500万 × 2% = 10万<br>
            <strong>合计税费约：15万</strong></p>`
          },
          { 
            id: 6, icon: 'clipboard', title: '二手房验房20项清单', 
            summary: '交房验房是最后一道关，20个验房要点帮你避免踩坑，附完整验房清单。',
            category: '避坑指南', tags: ['验房', '交房', '避坑'], readTime: 6, time: '2026-08-11',
            fullContent: `<h4>一、房屋结构</h4>
            <p>1. 检查墙面：有无裂缝、渗水、空鼓<br>
            2. 检查天花板：有无水渍、裂缝<br>
            3. 检查地面：是否平整、有无起砂<br>
            4. 检查门窗：密封性、开关是否顺畅</p>
            <h4>二、水电设施</h4>
            <p>5. 测试所有插座是否有电<br>
            6. 测试所有水龙头出水是否正常<br>
            7. 检查下水是否通畅（倒水测试）<br>
            8. 检查配电箱、空气开关标识</p>
            <h4>三、厨卫检查</h4>
            <p>9. 厨房：橱柜五金、台面、烟机灶具<br>
            10. 卫生间：防水、马桶、花洒<br>
            11. 瓷砖：空鼓率、勾缝是否完整</p>
            <h4>四、其他要点</h4>
            <p>12. 暖气：是否正常供暖<br>
            13. 天然气：表读数、是否通气<br>
            14. 空调：试机测试制冷制热<br>
            15. 灯具：所有灯具是否正常<br>
            16. 门锁：钥匙、门禁卡数量<br>
            17. 物业：物业费、水电费结清证明<br>
            18. 户口：确认是否已迁出<br>
            19. 附属设施：家具家电清单核对<br>
            20. 钥匙：门禁卡、信箱钥匙、车位钥匙</p>`
          },
          { 
            id: 7, icon: 'palette', title: '二手房装修改造指南', 
            summary: '老房改造从哪开始？硬装、软装、收纳...预算分配和装修顺序全攻略。',
            category: '装修指南', tags: ['装修', '改造', '预算'], readTime: 10, time: '2026-08-09',
            fullContent: `<h4>一、装修前准备</h4>
            <p>1. 明确需求：几人住、生活习惯、风格偏好<br>
            2. 确定预算：建议房价的10-20%作为装修预算<br>
            3. 找装修公司/施工队：对比3家以上，看工地<br>
            4. 设计方案：平面布局 → 效果图 → 施工图</p>
            <h4>二、装修顺序</h4>
            <p>1. 拆改：拆墙、铲墙皮、换门窗<br>
            2. 水电改造：水电定位 → 开槽 → 布管 → 验收<br>
            3. 泥瓦工：贴砖、防水、找平<br>
            4. 木工：吊顶、柜子、造型<br>
            5. 油漆工：墙面处理、刷漆<br>
            6. 安装：橱柜、地板、门、灯具<br>
            7. 软装：家具、家电、窗帘、装饰</p>
            <h4>三、预算分配参考</h4>
            <p>以100平中等装修为例（总预算15万）：<br>
            • 水电改造：1.5万<br>
            • 瓷砖/地板：2.5万<br>
            • 墙面/吊顶：2万<br>
            • 橱柜：2万<br>
            • 卫浴：1.5万<br>
            • 门/窗：1.5万<br>
            • 人工费：3万<br>
            • 其他/备用：1万</p>`
          },
          { 
            id: 8, icon: 'alert', title: '二手房常见陷阱避坑指南', 
            summary: '虚假房源、产权纠纷、户口问题、凶宅...买房必看的20个避坑要点。',
            category: '避坑指南', tags: ['避坑', '陷阱', '注意事项'], readTime: 8, time: '2026-08-08',
            fullContent: `<h4>一、房源陷阱</h4>
            <p><strong>1. 虚假房源：</strong>价格明显低于市场价的大概率是假房源，吸引客户打电话用的<br>
            <strong>2. 图片不符：</strong>精修图/户型图与实际不符，一定要实地看房<br>
            <strong>3. 凶宅隐瞒：</strong>可到物业、邻居处打听，或查新闻报道</p>
            <h4>二、产权陷阱</h4>
            <p><strong>4. 抵押房：</strong>房子有银行抵押，需业主先解押才能过户<br>
            <strong>5. 查封房：</strong>被法院查封的房子不能交易，过户前要查档<br>
            <strong>6. 共有房产：</strong>夫妻共有需双方签字，否则合同可能无效<br>
            <strong>7. 小产权房：</strong>没有房产证，不受法律保护，不能贷款</p>
            <h4>三、交易陷阱</h4>
            <p><strong>8. 阴阳合同：</strong>为避税签低价合同，买方再卖时个税更高，且有法律风险<br>
            <strong>9. 定金圈套：</strong>注意"定金"与"订金"区别，定金违约不退<br>
            <strong>10. 口头承诺：</strong>所有承诺都要写进合同，口头承诺不算数<br>
            <strong>11. 交房时间：</strong>明确交房日期，以及逾期交房违约金</p>
            <h4>四、户口与学区</h4>
            <p><strong>12. 户口不迁：</strong>合同约定户口迁出时间和违约金，可预留尾款<br>
            <strong>13. 学区占用：</strong>买学区房前确认学位是否被占用<br>
            <strong>14. 规划变更：</strong>周边规划可能变化，学区划分也可能调整</p>
            <h4>五、中介陷阱</h4>
            <p><strong>15. 吃差价：</strong>中介压低业主价、抬高买家价，两头吃<br>
            <strong>16. 乱收费：</strong>明确中介费包含哪些服务，避免额外收费<br>
            <strong>17. 独家委托：</strong>谨慎签独家委托，限制你选择其他中介</p>`
          },
        ];

        const filteredKnowledgeDocs = computed(() => {
          if (!knowledgeCat.value) return knowledgeDocs;
          return knowledgeDocs.filter(d => d.category === knowledgeCat.value);
        });

        function viewKnowledge(doc) {
          currentKnowledge.value = doc;
          showKnowledgeDetail.value = true;
        }

        // ===== 知识库管理（管理员：上传 / 列表 / 删除 / 重建索引） =====
        const kbDocs = ref([]);
        const kbTotal = ref(0);
        const kbPage = ref(1);
        const kbPageSize = 10;
        const kbLoading = ref(false);
        const kbUploading = ref(false);
        const kbRebuilding = ref(false);
        const kbSelectedFile = ref(null);
        const kbFileInput = ref(null);
        const kbMessage = ref('');
        const kbMessageType = ref('success');
        const kbStats = ref({
          document_count: 0, chunk_count: 0, total_size: 0,
          house_vectors: 0, chunk_size: 512, chunk_overlap: 50,
        });

        /** 知识库管理权限（后端 knowledge:manage，仅管理员拥有） */
        const canKnowledge = computed(() => can('knowledge:manage'));

        function kbNotify(text, type = 'success') {
          kbMessage.value = text;
          kbMessageType.value = type;
        }

        function formatFileSize(bytes) {
          const n = Number(bytes) || 0;
          if (n <= 0) return '-';
          if (n < 1024) return n + ' B';
          if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
          return (n / 1024 / 1024).toFixed(2) + ' MB';
        }

        /** 统计接口公开，普通用户也能看到「AI 已索引多少」 */
        async function loadKnowledgeStats() {
          try {
            const res = await api.get('/knowledge/stats');
            if (res.ok) kbStats.value = Object.assign({}, kbStats.value, await res.json());
          } catch (e) { /* 统计失败不影响页面 */ }
        }

        async function loadKnowledgeDocs() {
          if (!canKnowledge.value) return;
          kbLoading.value = true;
          try {
            const res = await api.get('/knowledge/documents?page=' + kbPage.value + '&page_size=' + kbPageSize);
            const data = await res.json();
            if (res.ok) {
              kbDocs.value = data.items || [];
              kbTotal.value = data.total || 0;
            } else {
              kbDocs.value = [];
              kbNotify(data.detail || '文档列表加载失败', 'error');
            }
          } catch (e) {
            kbDocs.value = [];
            kbNotify('网络错误，文档列表加载失败', 'error');
          }
          kbLoading.value = false;
        }

        function loadKnowledge() {
          loadKnowledgeStats();
          if (canKnowledge.value) loadKnowledgeDocs();
        }

        function onKnowledgeFile(e) {
          const file = (e.target.files && e.target.files[0]) || null;
          kbSelectedFile.value = file;
          if (file) kbNotify('');
        }

        async function uploadKnowledge() {
          const file = kbSelectedFile.value;
          if (!file) return;
          const ext = (file.name.split('.').pop() || '').toLowerCase();
          if (['pdf', 'docx', 'txt', 'md'].indexOf(ext) === -1) {
            kbNotify('仅支持 pdf / docx / txt / md 文件', 'error');
            return;
          }
          kbUploading.value = true;
          kbNotify('');
          try {
            const fd = new FormData();
            fd.append('file', file);
            const res = await api.upload('/knowledge/upload', fd);
            const data = await res.json();
            if (res.ok) {
              kbNotify(`「${data.filename}」已索引：切分为 ${data.chunk_count} 个片段，AI 问答现在可以引用它`, 'success');
              kbSelectedFile.value = null;
              if (kbFileInput.value) kbFileInput.value.value = '';
              kbPage.value = 1;
              await Promise.all([loadKnowledgeDocs(), loadKnowledgeStats()]);
            } else {
              kbNotify(data.detail || '上传失败', 'error');
            }
          } catch (e) {
            kbNotify('网络错误，上传失败', 'error');
          }
          kbUploading.value = false;
        }

        async function removeKnowledgeDoc(doc) {
          if (!confirm(`确定删除「${doc.filename}」吗？\n删除后它的 ${doc.chunk_count} 个片段会从向量库移除，AI 问答不再引用。`)) return;
          try {
            const res = await api.del('/knowledge/documents/' + encodeURIComponent(doc.doc_id));
            const data = await res.json();
            if (res.ok) {
              kbNotify(data.message || '已删除', 'success');
              await Promise.all([loadKnowledgeDocs(), loadKnowledgeStats()]);
            } else {
              kbNotify(data.detail || '删除失败', 'error');
            }
          } catch (e) {
            kbNotify('网络错误，删除失败', 'error');
          }
        }

        async function rebuildHouseIndex() {
          if (!confirm('重建房源向量索引会重新抽样并写入向量库，耗时约 1-3 分钟，确定继续吗？')) return;
          kbRebuilding.value = true;
          kbNotify('');
          try {
            const res = await api.request('/knowledge/index-houses?limit=1000', { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
              kbNotify(data.message || '房源索引重建完成', 'success');
              await loadKnowledgeStats();
            } else {
              kbNotify(data.detail || '索引重建失败', 'error');
            }
          } catch (e) {
            kbNotify('网络错误，索引重建失败', 'error');
          }
          kbRebuilding.value = false;
        }

        function formatPrice(price) {
          if (!price && price !== 0) return '';
          if (typeof price === 'number') return Math.round(price).toLocaleString();
          const p = String(price).replace('万', '').replace('元', '').replace(/,/g, '');
          const num = parseFloat(p);
          if (isNaN(num)) return price;
          return Math.round(num).toLocaleString();
        }

        function switchTab(key) {
          mobileMenuOpen.value = false;
          // 离开详情页时清空当前房源，并抹掉地址栏里的 ?house=，避免刷新后又跳回详情
          if (key !== 'detail') {
            detailHouse.value = null;
            if (window.location.search.indexOf('house=') !== -1) {
              try { window.history.replaceState({}, '', window.location.pathname); } catch (e) { /* ignore */ }
            }
          }
          tab.value = key;
          if (key === 'find') {
            if (findView.value === 'map') {
              loadMapHouses();
              nextTick(() => initMap());
            } else {
              loadHouses();
            }
          }
          if (key === 'home') loadStats();
          if (key === 'recommend') loadRecommend();
          // 问答页 DOM 被 v-else-if 重建过，重新挂载小地图
          if (key === 'chat' && chatView.value === 'chat') {
            nextTick(() => renderAllChatMaps());
          }
          if (key === 'favorites') loadFavorites();
          if (key === 'compare') loadCompare();
          if (key === 'history') loadHistory();
          if (key === 'price') {
            // 估价页右侧要展示区域行情，进来就先取一次（内部有缓存）
            loadMarketOverview();
            loadEstHistory();
            if (priceView.value === 'trend') loadTrendRegions();
          }
          if (key === 'dashboard') loadDashboard();
          // 知识库已并入 AI 问答页，进入时按当前子视图加载
          if (key === 'chat' && chatView.value === 'kb') loadKnowledge();
          window.scrollTo(0, 0);
        }

        /** 找房页：列表 / 地图 视图切换 */
        function setFindView(view) {
          if (findView.value === view) return;
          findView.value = view;
          if (view === 'map') {
            loadMapHouses();
            nextTick(() => initMap());
          }
        }

        /** AI 问答页：对话 / 知识库 视图切换 */
        function setChatView(view) {
          if (chatView.value === view) return;
          chatView.value = view;
          if (view === 'kb') loadKnowledge();
          // 从「知识库」切回「智能问答」时消息列表 DOM 同样是 v-if 重建的，
          // 必须重挂地图，否则地图卡片在、地图区域空白
          if (view === 'chat') nextTick(() => renderAllChatMaps());
        }

        /** 房价页：估价 / 走势 视图切换 */
        function setPriceView(view) {
          if (priceView.value === view) return;
          priceView.value = view;
          if (view === 'trend') loadTrendRegions();
        }

        /** 打开找房页并指定视图（供首页快捷入口/空状态复用） */
        function openFind(view = 'list') {
          findView.value = view;
          switchTab('find');
        }

        /** 打开房价页并指定视图 */
        function openPrice(view = 'estimate') {
          priceView.value = view;
          switchTab('price');
        }

        // ===== 登录 Token 与权限（身份在登录时确定） =====
        const authToken = ref(localStorage.getItem('house_token') || '');
        if (authToken.value) api.setAuthToken(authToken.value);

        /** 当前登录用户是否为管理员（依据登录接口返回的 role，保留作为兼容判断） */
        const isAdmin = computed(() => !!(currentUser.value && currentUser.value.role === 'admin') && !!authToken.value);

        // ===== 最小 RBAC：权限码 =====
        /** 全部权限码（仅用于旧登录态按 role 回退，不参与实际校验） */
        const ALL_PERMISSIONS = [
          'house:read', 'house:manage', 'house:create',
          'house:update', 'house:delete', 'user:change-password',
          'knowledge:manage',
        ];
        /** 普通用户默认权限（后端未返回 permissions 时的兜底） */
        const BASIC_PERMISSIONS = ['house:read', 'user:change-password'];

        /** 当前用户权限码列表，来源：登录/注册响应 user.permissions 或 localStorage 恢复 */
        const permissions = ref([]);

        /** 权限判断：模板与脚本通用，返回布尔值 */
        const can = (code) => permissions.value.indexOf(code) !== -1;

        /**
         * 归一化权限：优先用后端下发的 permissions；
         * 兼容旧登录态（house_user 无 permissions 字段）：admin → 全量，其余 → 基础权限
         */
        function normalizePermissions(user) {
          if (!user) return [];
          const list = Array.isArray(user.permissions)
            ? user.permissions.filter((p) => typeof p === 'string')
            : [];
          if (list.length) return list;
          if (user.role === 'admin') return ALL_PERMISSIONS.slice();
          return BASIC_PERMISSIONS.slice();
        }

        /** 从登录/注册响应中取 user，并兼容 permissions 放在响应顶层的实现 */
        function extractUser(data) {
          if (!data || !data.user) return null;
          const user = Object.assign({}, data.user);
          if (!Array.isArray(user.permissions) && Array.isArray(data.permissions)) {
            user.permissions = data.permissions;
          }
          return user;
        }

        /** 登录 / 注册 / 恢复会话统一入口：同步 currentUser、permissions 与 localStorage */
        function setSessionUser(user) {
          if (!user) {
            currentUser.value = null;
            permissions.value = [];
            try { localStorage.removeItem('house_user'); } catch (e) {}
            return;
          }
          const perms = normalizePermissions(user);
          permissions.value = perms;
          currentUser.value = Object.assign({}, user, { permissions: perms });
          try { localStorage.setItem('house_user', JSON.stringify(currentUser.value)); } catch (e) {}
        }

        /** 管理入口：拥有 house:manage 或 role==='admin'（且已登录拿到 Token） */
        const canManage = computed(() =>
          !!authToken.value &&
          (can('house:manage') || !!(currentUser.value && currentUser.value.role === 'admin'))
        );

        /** 当前用户展示名：兼容 name / username，避免出现 undefined */
        const displayName = computed(() => {
          const u = currentUser.value;
          if (!u) return '';
          return u.name || u.username || u.email || '用户';
        });
        /** 头像文字（昵称首字母） */
        const avatarText = computed(() => {
          const n = displayName.value;
          return n ? n.charAt(0).toUpperCase() : 'U';
        });

        /** 导航项按权限过滤：无 house:manage（且非 admin）看不到「管理后台」入口 */
        const visibleNavItems = computed(() =>
          navItems.filter((item) => item.key !== 'admin' || canManage.value)
        );

        /** 打开独立后台（新标签页，前台状态不受影响）；记住句柄，退出登录时才能把这个标签关掉 */
        let adminWindow = null;
        function openAdmin() {
          adminWindow = window.open('/admin', '_blank');
        }

        /** 统一处理 401（凭证失效）/ 403（无权限） */
        function handleAuthError(res) {
          if (res.status === 401) {
            alert('登录凭证已失效，请重新登录');
            return true;
          }
          if (res.status === 403) {
            alert('当前账号没有该操作权限');
            return true;
          }
          return false;
        }

        // ===== 修改密码 =====
        const showPwdModal = ref(false);
        const pwdForm = ref({ old_password: '', new_password: '', confirm_password: '' });
        const pwdLoading = ref(false);
        const pwdError = ref('');
        const pwdSuccess = ref('');
        /** 密码显示切换 */
        const pwdVisible = ref({ login: false, register: false, old: false, new: false });

        /** 新密码强度提示 */
        const pwdStrength = computed(() => {
          const p = pwdForm.value.new_password || '';
          if (!p) return { level: 0, text: '', cls: '' };
          let score = 0;
          if (p.length >= 6) score += 1;
          if (p.length >= 10) score += 1;
          if (/[a-zA-Z]/.test(p) && /\d/.test(p)) score += 1;
          if (/[^a-zA-Z0-9]/.test(p)) score += 1;
          const levels = [
            { level: 1, text: '弱', cls: 'weak' },
            { level: 2, text: '一般', cls: 'fair' },
            { level: 3, text: '较强', cls: 'good' },
            { level: 4, text: '很强', cls: 'strong' },
          ];
          return levels[Math.min(Math.max(score, 1), 4) - 1];
        });

        function openPwdModal() {
          showUserMenu.value = false;
          if (!currentUser.value || !authToken.value) {
            showPwdModal.value = false;
            loginError.value = '请先登录后再修改密码';
            showLoginModal.value = true;
            return;
          }
          pwdForm.value = { old_password: '', new_password: '', confirm_password: '' };
          pwdError.value = '';
          pwdSuccess.value = '';
          pwdVisible.value.old = false;
          pwdVisible.value.new = false;
          showPwdModal.value = true;
        }

        /** 登录页的「修改密码」入口 */
        function openPwdFromLogin() {
          showLoginModal.value = false;
          openPwdModal();
        }

        async function doChangePassword() {
          const form = pwdForm.value;
          pwdError.value = '';
          pwdSuccess.value = '';

          if (!form.old_password) { pwdError.value = '请输入原密码'; return; }
          if (!form.new_password || form.new_password.length < 6) { pwdError.value = '新密码至少 6 位'; return; }
          if (form.new_password !== form.confirm_password) { pwdError.value = '两次输入的新密码不一致'; return; }
          if (form.new_password === form.old_password) { pwdError.value = '新密码不能与原密码相同'; return; }

          pwdLoading.value = true;
          try {
            const res = await api.post('/user/change-password', {
              old_password: form.old_password,
              new_password: form.new_password,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
              if (handleAuthError(res)) return;
              pwdError.value = data.detail || '修改失败';
              return;
            }
            pwdSuccess.value = data.message || '密码修改成功';
            pwdForm.value = { old_password: '', new_password: '', confirm_password: '' };

            if (data.role === 'admin') {
              // 管理员改密后强制重新登录，避免旧 Token 继续可用
              setTimeout(function () {
                showPwdModal.value = false;
                doLogout();
                alert('管理员密码已修改，请使用新密码重新登录');
              }, 1000);
            } else {
              setTimeout(function () { showPwdModal.value = false; }, 1200);
            }
          } catch (e) {
            pwdError.value = '修改失败：' + e;
          } finally {
            pwdLoading.value = false;
          }
        }

        function doSearch() {
          findView.value = 'list';
          housePage.value = 1;
          // switchTab('find') 内部会带着 searchKw 重新拉列表
          switchTab('find');
        }

        /** 顶部导航搜索：把导航框自己的关键词交给列表查询，不与 hero 输入框互相回填 */
        function doSearchFromHeader() {
          searchKw.value = (headerKw.value || '').trim();
          doSearch();
        }

        /** 区域下拉：从后端取全部区域（只含北京行政区县） */
        async function loadRegions() {
          try {
            const res = await api.get('/houses/regions');
            const data = await res.json();
            const rows = (data.regions || []).filter(r => r.name);
            const list = rows.map(r => r.name);
            if (list.length) regions.value = list;
            // 首页区域卡片要带房源数，这里顺手留一份（已按房源数倒序）
            homeRegions.value = rows.slice(0, 8);
          } catch(e) { console.error(e); }
        }

        const stats = ref({
          totalHouses: 0,
          totalRegions: 0,
          avgPrice: '0',
        });
        /** 首页概览：全部走后端全量口径，不再用首页 50 条估算 */
        async function loadStats() {
          try {
            const res = await api.get('/houses/stats');
            const data = await res.json();
            stats.value.totalHouses = data.total || 0;
            stats.value.totalRegions = data.region_count || 0;
            stats.value.avgPrice = data.avg_price
              ? Math.round(data.avg_price).toLocaleString()
              : '0';
          } catch(e) {
            console.error(e);
          }
        }
        async function loadHouses() {
          try {
            const params = new URLSearchParams();
            // 首页/顶部搜索框的关键词真正参与查询（原来只切 tab，等于没搜）
            const kw = (searchKw.value || '').trim();
            if (kw) params.append('keyword', kw);
            if (filter.value.region) params.append('region', filter.value.region);
            if (filter.value.rooms) params.append('rooms', filter.value.rooms);
            if (filter.value.priceRange) {
              const [min, max] = filter.value.priceRange.split('-');
              if (min) params.append('price_min', min);
              if (max) params.append('price_max', max);
            }
            params.append('page', housePage.value);
            params.append('page_size', housePageSize.value);
            const res = await api.get('/houses?' + params.toString());
            const data = await res.json();
            houses.value = data.houses || data.items || [];
            totalHouses.value = data.total || houses.value.length;
          } catch(e) { console.error(e); }
        }

        function changeHousePage(delta) {
          const totalPages = Math.ceil(totalHouses.value / housePageSize.value) || 1;
          const newPage = housePage.value + delta;
          if (newPage < 1 || newPage > totalPages) return;
          housePage.value = newPage;
          loadHouses();
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        /** 输入页码跳转：越界自动收敛到 [1, 总页数]，非法输入回到第 1 页 */
        function jumpHousePage() {
          let target = parseInt(pageJump.value, 10);
          if (!target || target < 1) target = 1;
          if (target > houseTotalPages.value) target = houseTotalPages.value;
          pageJump.value = '';
          if (target === housePage.value) return;
          housePage.value = target;
          loadHouses();
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        async function loadHotHouses() {
          try {
            // 8 条 = 4 列 × 2 行，首页网格不留半行空位
            const res = await api.get('/houses/hot?limit=8');
            const data = await res.json();
            hotHouses.value = data.houses || [];
          } catch(e) { console.error(e); }
        }

        async function loadRecommend() {
          try {
            // 模块 B：协同过滤 + 用户画像
            // 注意：管理员存在 admin 表，id 与 user_info 表无关，传 0 走冷启动，
            // 否则会误用 user_info 里同 id 用户的画像；未登录仍用 user1 做功能演示。
            const u = currentUser.value;
            const uid = !u ? 1 : (u.role === 'admin' ? 0 : (u.id || 0));
            const res = await api.get('/analysis/recommend/v2?user_id=' + uid + '&limit=12');
            const data = await res.json();
            if (data.status === 'error') throw new Error(data.error);
            recHouses.value = data.recommendations || [];
            userProfile.value = data.profile || null;
            recStrategy.value = data.strategy || '';
          } catch(e) {
            console.error(e);
            userProfile.value = null;
            recStrategy.value = '';
            try {
              const res = await api.get('/houses/recommendations?limit=9');
              const data = await res.json();
              recHouses.value = data.recommendations || data.houses || [];
            } catch(e2) { console.error(e2); }
          }
        }

        async function loadMapHouses() {
          try {
            const params = new URLSearchParams();
            if (mapRegion.value) params.append('region', mapRegion.value);
            params.append('limit', 100000);
            const res = await api.get('/houses/map/all?' + params.toString());
            const data = await res.json();
            mapHousesAll.value = data.houses || [];
            mapHousesTotal.value = data.total || mapHousesAll.value.length;
            mapPage.value = 1;
            applyMapPage();
            // 切换地区后由 renderMapMarkers 统一把地图定位到该区中心 / 全城范围
            renderMapMarkers();
          } catch(e) { console.error(e); }
        }

        function applyMapPage() {
          const start = (mapPage.value - 1) * mapPageSize.value;
          mapHouses.value = mapHousesAll.value.slice(start, start + mapPageSize.value);
          // 地图标记使用全部房源数据，翻页时无需重绘地图
        }

        function changeMapPage(delta) {
          const totalPages = Math.ceil(mapHousesAll.value.length / mapPageSize.value) || 1;
          const newPage = mapPage.value + delta;
          if (newPage < 1 || newPage > totalPages) return;
          mapPage.value = newPage;
          applyMapPage();
        }

        // 高德 SDK 改为运行时加载：Key / 安全密钥由后端 /api/config/map 下发，
        // 源码里不出现密钥（仓库要公开）。多次调用只注入一次；失败时保持
        // window.AMap 未定义，initMap 的轮询会继续重试。
        let amapSdkPromise = null;
        function loadAmapSdk() {
          if (window.AMap || amapSdkPromise) return amapSdkPromise;
          amapSdkPromise = fetch('/api/config/map')
            .then((r) => r.json())
            .then((cfg) => {
              if (!cfg || !cfg.key) {
                console.warn('地图 Key 未配置：请在 .env 设置 AMAP_JS_KEY');
                amapSdkPromise = null;
                return null;
              }
              window._AMapSecurityConfig = { securityJsCode: cfg.security_code || '' };
              return new Promise((resolve, reject) => {
                const s = document.createElement('script');
                s.src = 'https://webapi.amap.com/maps?v=2.0&key=' + cfg.key;
                s.onload = () => resolve(window.AMap);
                s.onerror = () => reject(new Error('AMap SDK 加载失败'));
                document.head.appendChild(s);
              });
            })
            .catch((e) => {
              console.warn('地图 SDK 加载失败:', e);
              amapSdkPromise = null;
              return null;
            });
          return amapSdkPromise;
        }

        function initMap() {
          // Vue v-else-if 切换 tab 时会销毁并重建 #mapContainer DOM，
          // 旧 AMap 实例仍绑定在已销毁的 DOM 上，必须销毁重建
          if (window._mapInstance) {
            try { window._mapInstance.destroy(); } catch(e) {}
            window._mapInstance = null;
            mapLoaded.value = false;
          }
          if (typeof AMap === 'undefined') {
            loadAmapSdk();          // 首次进入时向 /api/config/map 取 Key 并注入 SDK
            setTimeout(initMap, 500);
            return;
          }
          try {
            window._mapInstance = new AMap.Map('mapContainer', {
              zoom: 11,
              center: [116.4074, 39.9042],
              mapStyle: 'amap://styles/normal',
            });
            mapLoaded.value = true;
            renderMapMarkers();
          } catch(e) { console.error('Map init error:', e); }
        }

        function renderMapMarkers() {
          if (!window._mapInstance) return;
          window._mapInstance.clearMap();
          if (window._mapZoomHandler) {
            window._mapInstance.off('zoomend', window._mapZoomHandler);
            window._mapInstance.off('moveend', window._mapZoomHandler);
            window._mapZoomHandler = null;
          }

          // 关闭上一次打开的房源气泡，避免切区后旧气泡还留在地图上
          if (window._mapInfoWindow) {
            try { window._mapInfoWindow.close(); } catch(e) {}
            window._mapInfoWindow = null;
          }
          // 切区/重绘时清掉上一个选中房源的高亮标点
          clearFocusPin();

          // 地图标记使用全部房源数据（而非当前页），保证地图能标识所有房源
          let source = mapHousesAll.value.length ? mapHousesAll.value : mapHouses.value;
          const validHouses = source.filter(h => h.longitude && h.latitude);
          const regionCenter = mapRegion.value ? MAP_REGION_CENTERS[mapRegion.value] : null;
          if (validHouses.length === 0) {
            // 该区域没有可展示房源时，也把地图定位到区中心，避免停留在上一个地区
            if (regionCenter) {
              try { window._mapInstance.setZoomAndCenter(12, regionCenter); } catch(e) {}
            }
            return;
          }

          // 预采样：超过 3000 条时均匀采样到最多 3000 条，防止大数据量卡顿
          let displayHouses = validHouses;
          if (validHouses.length > 3000) {
            const step = Math.ceil(validHouses.length / 3000);
            displayHouses = [];
            for (let i = 0; i < validHouses.length; i += step) displayHouses.push(validHouses[i]);
          }

          if (displayHouses.length > 300) {
            // 数据量大，用圆形标记 + 分级采样，性能好
            renderMarkersFast(displayHouses);
          } else {
            // 数据量小，普通标记 + 价格标签
            const markers = [];
            displayHouses.forEach((house) => {
              const marker = new AMap.Marker({
                position: [parseFloat(house.longitude), parseFloat(house.latitude)],
                title: house.title,
                offset: new AMap.Pixel(-12, -30),
                content: '<div style="background:#00ae66;color:#fff;padding:5px 12px;border-radius:14px;font-size:12px;font-weight:600;white-space:nowrap;box-shadow:0 2px 8px rgba(0,174,102,0.4);border:2px solid #fff;">' +
                         formatPrice(house.price) + '万</div>',
              });
              marker.on('click', function() { showHouseInfo(house, marker); });
              markers.push(marker);
              window._mapInstance.add(marker);
            });
          }

          // 统一在数据加载完成后定位：切到具体地区时居中并缩放到该区，全部区域则回到全城。
          // 这里按房源坐标算包围盒（而不是用已渲染的点），避免当前视野内没有点时无法定位。
          let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
          displayHouses.forEach((h) => {
            const lng = parseFloat(h.longitude), lat = parseFloat(h.latitude);
            if (isNaN(lng) || isNaN(lat)) return;
            minLng = Math.min(minLng, lng); maxLng = Math.max(maxLng, lng);
            minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
          });
          if (regionCenter) {
            // 选中具体地区：直接定位到区中心（房源坐标偶有偏差时也不会跑偏）
            try { window._mapInstance.setZoomAndCenter(12, regionCenter); } catch (e) {}
          } else if (isFinite(minLng) && isFinite(minLat)) {
            // 全部区域：按房源包围盒自适应，回到全城视野
            try {
              window._mapInstance.setBounds(
                new AMap.Bounds([minLng, minLat], [maxLng, maxLat]),
                false, [60, 60, 60, 380], 11
              );
            } catch (e) { /* ignore */ }
          }
        }

        // 高性能渲染：圆形标记 + 分级采样（根据缩放级别显示不同密度）
        // 高性能渲染：醒目的橙色圆点 + 分级采样 + 悬停效果 + 放大显示价格
        function renderMarkersFast(houses) {
          const map = window._mapInstance;
          if (!map) return;

          function updateMarkers() {
            if (window._mapCircles) {
              map.remove(window._mapCircles);
              window._mapCircles = [];
            }
            if (window._mapLabels) {
              map.remove(window._mapLabels);
              window._mapLabels = [];
            }

            const zoom = map.getZoom();
            // 只对当前视野内的房源采样：低倍看全城密度，放大后局部点全部保留
            const bounds = map.getBounds();
            const viewHouses = houses.filter(function(h) {
              const lng = parseFloat(h.longitude);
              const lat = parseFloat(h.latitude);
              if (isNaN(lng) || isNaN(lat)) return false;
              return !bounds || bounds.contains([lng, lat]);
            });
            const maxVisible = Math.min(
              2200, Math.max(900, Math.floor(1200 * Math.pow(1.3, zoom - 11)))
            );
            const visibleCount = Math.min(viewHouses.length, maxVisible);
            const step = Math.max(1, Math.floor(viewHouses.length / Math.max(visibleCount, 1)));

            const circles = [];
            const labels = [];
            for (let i = 0; i < viewHouses.length; i += step) {
              const house = viewHouses[i];
              const lng = parseFloat(house.longitude);
              const lat = parseFloat(house.latitude);
              if (isNaN(lng) || isNaN(lat)) continue;

              // 用像素级圆点（CircleMarker）：按米算半径在城市级缩放下不足 1px，等于看不见
              const baseRadius = zoom >= 15 ? 7 : (zoom >= 13 ? 6 : 5);
              const circle = new AMap.CircleMarker({
                center: [lng, lat],
                radius: baseRadius,
                strokeColor: '#ffffff',
                strokeOpacity: 1,
                strokeWeight: 2,
                fillColor: '#ff5e00',
                fillOpacity: 0.95,
                zIndex: 200,
                bubble: true,
              });
              circle._house = house;
              circle._baseRadius = baseRadius;
              circle.on('click', function() { showHouseInfo(house, circle); });
              circle.on('mouseover', function() {
                circle.setOptions({
                  fillOpacity: 1,
                  strokeWeight: 3,
                  radius: baseRadius + 3,
                  zIndex: 300,
                });
              });
              circle.on('mouseout', function() {
                circle.setOptions({
                  fillOpacity: 0.95,
                  strokeWeight: 2,
                  radius: baseRadius,
                  zIndex: 200,
                });
              });
              circles.push(circle);

              // 缩放较大时显示价格标签
              if (zoom >= 14) {
                const label = new AMap.Marker({
                  position: [lng, lat],
                  offset: new AMap.Pixel(-35, -baseRadius - 25),
                  content: '<div style="background:#ff5e00;color:#fff;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;white-space:nowrap;border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.3);">' +
                           formatPrice(house.price) + '万</div>',
                  zIndex: 250,
                  bubble: true,
                });
                label._house = house;
                label.on('click', function() { showHouseInfo(house, label); });
                labels.push(label);
              }
            }
            map.add(circles);
            window._mapCircles = circles;
            if (labels.length > 0) {
              map.add(labels);
              window._mapLabels = labels;
            }
            // 重绘后如果还有选中的房源，重新收起与它重叠的价格标签
            if (window._mapFocusPin) {
              const pp = window._mapFocusPin.getPosition();
              if (pp) hideNearbyLabels([pp.lng, pp.lat]);
            }
          }

          updateMarkers();

          // 缩放变化时重新调整显示数量
          let timer = null;
          window._mapZoomHandler = function() {
            clearTimeout(timer);
            timer = setTimeout(updateMarkers, 120);
          };
          map.on('zoomend', window._mapZoomHandler);
          map.on('moveend', window._mapZoomHandler);
        }

        function showHouseInfo(house, marker) {
          // 同一时间只保留一个房源气泡
          if (window._mapInfoWindow) {
            try { window._mapInfoWindow.close(); } catch(e) {}
          }
          const infoWindow = new AMap.InfoWindow({
            content: '<div style="padding:10px;min-width:220px;">' +
              '<div style="font-weight:600;font-size:13px;margin-bottom:8px;">' + house.title + '</div>' +
              '<div style="color:#ff5e00;font-weight:700;font-size:16px;margin-bottom:6px;">' + house.price + '</div>' +
              '<div style="font-size:12px;color:#666;margin-bottom:4px;">' + house.rooms + ' · ' + house.area + '</div>' +
              '<div style="font-size:12px;color:#888;margin-bottom:10px;"><svg class="ic" style="width:12px;height:12px;vertical-align:-2px"><use href="#i-map-pin"></use></svg> ' + house.address + '</div>' +
              '<button onclick="window._viewHouseById && window._viewHouseById(' + house.id + ')" style="width:100%;padding:7px;background:#00ae66;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;font-weight:500;">查看房源详情</button>' +
              '</div>',
            offset: new AMap.Pixel(0, -48),
          });
          const pos = marker.getPosition ? marker.getPosition() : marker.getCenter();
          infoWindow.open(window._mapInstance, pos);
          window._mapInfoWindow = infoWindow;
        }

        /**
         * AI 问答内的房源小地图：把本轮 RAG 召回（且带经纬度）的房源打点。
         * 与找房页地图是两套实例（独立容器、独立生命周期），切 tab 时 DOM 会重建，需重新 init。
         */
        function renderChatMap(msgIdx) {
          const idx = msgIdx;
          const list = (messages.value[idx] && messages.value[idx].mapHouses) || [];
          // 外地城市：画高德返回的商圈/地铁点（灰色），明确区别于房源
          const pois = (messages.value[idx] && messages.value[idx].externalPois) || [];
          const el = document.getElementById('chatMap-' + idx);
          if (!el || (!list.length && !pois.length)) return;
          if (typeof AMap === 'undefined') { setTimeout(() => renderChatMap(idx), 500); return; }
          // 切 tab 回来时 v-if 刚重建 DOM，浏览器可能还没完成 layout，容器是 0×0。
          // 此刻 new AMap.Map 会建出一个 0×0 画布，且之后不会自愈——表现就是
          // 「卡片标题都在、地图区域一片空白」。所以等容器真有尺寸再初始化。
          if (!el.clientWidth || !el.clientHeight) {
            const tries = (parseInt(el.dataset.mapRetry || '0', 10) || 0) + 1;
            el.dataset.mapRetry = String(tries);
            if (tries <= 20) setTimeout(() => renderChatMap(idx), 100);
            return;
          }
          // 每条带地图的回答各自持有一个实例（历史对话的地图保留，切 tab 后按需重建）
          window._chatMaps = window._chatMaps || {};
          if (window._chatMaps[idx]) {
            try { window._chatMaps[idx].destroy(); } catch (e) {}
            delete window._chatMaps[idx];
          }
          const map = new AMap.Map('chatMap-' + idx, {
            zoom: 12,
            center: [116.4074, 39.9042],
            mapStyle: 'amap://styles/normal',
          });
          window._chatMaps[idx] = map;
          // 兜底：容器尺寸可能在地图创建之后才最终确定（滚动条出现/字体加载等），
          // 再 resize 一次，避免画布尺寸与实际容器不一致。
          setTimeout(() => { try { map.resize(); } catch (e) { /* 已销毁 */ } }, 300);

          // 同一坐标（同小区多套）合并成一个点，否则价格气泡互相遮挡、点不中
          const groups = new Map();
          list.forEach(h => {
            const lng = parseFloat(h.longitude);
            const lat = parseFloat(h.latitude);
            if (isNaN(lng) || isNaN(lat)) return;
            const key = lng.toFixed(4) + ',' + lat.toFixed(4);
            if (!groups.has(key)) groups.set(key, { lng: lng, lat: lat, houses: [] });
            groups.get(key).houses.push(h);
          });

          // 供弹窗里的"房源行"点击回到详情页（不用闭包，避免重新渲染后引用旧列表）
          window._chatHouseMap = {};
          groups.forEach(g => g.houses.forEach(h => { window._chatHouseMap[h.id] = h; }));
          window._chatOpenHouse = function (id) {
            const h = window._chatHouseMap && window._chatHouseMap[id];
            if (h) viewHouse(h);
          };

          let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
          groups.forEach(g => {
            minLng = Math.min(minLng, g.lng); maxLng = Math.max(maxLng, g.lng);
            minLat = Math.min(minLat, g.lat); maxLat = Math.max(maxLat, g.lat);
            const many = g.houses.length > 1;
            const marker = new AMap.Marker({
              position: [g.lng, g.lat],
              offset: new AMap.Pixel(-30, -16),
              zIndex: many ? 200 : 120,  // 合并点压在上面，避免被单套价格气泡盖住点不到
              content: '<div class="chat-pin' + (many ? ' multi' : '') + '">'
                + (many ? g.houses.length + ' 套' : formatPrice(g.houses[0].price) + '万') + '</div>',
            });
            marker.on('click', function () { openChatMapInfo(g, map); });
            map.add(marker);
          });

          // 外部地理点：小圆点 + 名称小标签，点击看来源
          pois.forEach(p => {
            minLng = Math.min(minLng, p.lng); maxLng = Math.max(maxLng, p.lng);
            minLat = Math.min(minLat, p.lat); maxLat = Math.max(maxLat, p.lat);
            const dot = new AMap.Marker({
              position: [p.lng, p.lat],
              offset: new AMap.Pixel(-5, -5),
              zIndex: 110,
              content: '<div class="poi-dot"></div>',
            });
            dot.on('click', function () { openPoiInfo(p, map); });
            map.add(dot);
            map.add(new AMap.Marker({
              position: [p.lng, p.lat],
              offset: new AMap.Pixel(9, -9),
              zIndex: 111,
              content: '<div class="poi-label">' + p.name + '</div>',
            }));
          });

          const pointCount = groups.size + pois.length;
          try {
            if (pointCount === 1) {
              map.setZoomAndCenter(15, [minLng, minLat]);
            } else if (isFinite(minLng) && isFinite(minLat)) {
              map.setBounds(new AMap.Bounds([minLng, minLat], [maxLng, maxLat]), false, [40, 40, 40, 40]);
            }
          } catch (e) { /* 视野自适应失败不影响打点 */ }
        }

        /** 切 tab 后 DOM 被重建：把历史消息里所有地图按顺序重新挂载 */
        function renderAllChatMaps() {
          // v-if 重建后必须等浏览器完成 layout 再建地图，否则高德拿到 0×0 容器。
          // 双 rAF：第一帧等样式计算，第二帧等布局与绘制。nextTick 只管 Vue 的 DOM patch，
          // 不管浏览器 layout，这是之前「切回来地图空白」的根因。
          requestAnimationFrame(() => requestAnimationFrame(() => {
            messages.value.forEach((m, i) => {
              // 外地城市的回答只有高德商圈/地铁点、没有房源，也要重挂
              const hasHouses = m.mapHouses && m.mapHouses.length;
              const hasPois = m.externalPois && m.externalPois.length;
              if (hasHouses || hasPois) renderChatMap(i);
            });
          }));
        }

        /** 小地图点位弹窗：同一小区多套时列成清单，点某一行进详情 */
        function openChatMapInfo(group, map) {
          if (!map) return;
          if (window._chatInfoWindow) {
            try { window._chatInfoWindow.close(); } catch (e) {}
          }
          const rows = group.houses.map(h =>
            '<div class="chat-info-row" onclick="window._chatOpenHouse && window._chatOpenHouse(' + h.id + ')">'
              + '<b>' + formatPrice(h.price) + '万</b>'
              + '<span>' + (h.title || '房源') + '</span>'
            + '</div>').join('');
          const win = new AMap.InfoWindow({
            content: '<div class="chat-info">' + rows + '</div>',
            offset: new AMap.Pixel(0, -14),
          });
          win.open(map, [group.lng, group.lat]);
          window._chatInfoWindow = win;
        }

        /** 外部地理点弹窗：说明这是高德地理数据、不是房源，避免误读 */
        function openPoiInfo(poi, map) {
          if (!map) return;
          if (window._chatInfoWindow) {
            try { window._chatInfoWindow.close(); } catch (e) {}
          }
          const html = '<div class="poi-info">'
            + '<b>' + poi.name + '</b>'
            + '<div class="poi-meta">' + (poi.district || '') + ' · ' + (poi.type || '地点') + '</div>'
            + '<div class="poi-src">数据来源：' + (poi.source || '高德') + ' · 不含房源价格</div>'
            + '</div>';
          const win = new AMap.InfoWindow({ content: html, offset: new AMap.Pixel(0, -10) });
          win.open(map, [poi.lng, poi.lat]);
          window._chatInfoWindow = win;
        }

        /** 清除「当前选中房源」的高亮标点，并恢复被临时隐藏的邻近价格标签 */
        function clearFocusPin() {
          if (window._mapFocusPin && window._mapInstance) {
            try { window._mapInstance.remove(window._mapFocusPin); } catch(e) {}
          }
          window._mapFocusPin = null;
          (window._mapHiddenLabels || []).forEach(m => {
            try { m.show(); } catch(e) {}
          });
          window._mapHiddenLabels = [];
        }

        /** 选中房源时，把紧邻的价格标签暂时收起，避免和红色标点叠在一起看不清 */
        function hideNearbyLabels(pos) {
          window._mapHiddenLabels = window._mapHiddenLabels || [];
          (window._mapLabels || []).forEach(m => {
            const p = m.getPosition && m.getPosition();
            if (!p || typeof p.lng !== 'number') return;
            // 约 45 米内视为同一栋楼，标签会与选中的标点重叠
            if (Math.abs(p.lng - pos[0]) < 0.0005 && Math.abs(p.lat - pos[1]) < 0.0005) {
              try { m.hide(); window._mapHiddenLabels.push(m); } catch(e) {}
            }
          });
        }

        function focusOnMap(house, i) {
          if (window._mapInstance && house.longitude && house.latitude) {
            const pos = [parseFloat(house.longitude), parseFloat(house.latitude)];
            window._mapInstance.setZoomAndCenter(16, pos);

            // 高亮闪烁：与该房源重合的圆点放大（可能因采样/重叠而找不到）
            if (window._mapCircles) {
              const target = window._mapCircles.find(c => c._house && c._house.id === house.id);
              if (target) {
                const baseR = target.getRadius();
                let count = 0;
                const blink = setInterval(() => {
                  if (count >= 4) {
                    clearInterval(blink);
                    target.setOptions({ fillOpacity: 0.9, radius: baseR, strokeWeight: 3 });
                    return;
                  }
                  if (count % 2 === 0) {
                    target.setOptions({ fillOpacity: 1, radius: baseR * 1.6, strokeWeight: 4 });
                  } else {
                    target.setOptions({ fillOpacity: 0.9, radius: baseR, strokeWeight: 3 });
                  }
                  count++;
                }, 200);
              }
            }

            // 关键：无论该坐标上有没有渲染出的圆点、是否与其他房源坐标重合，
            // 都为「当前选中的这套房」单独放一个置顶标点，保证列表点哪套、地图上就标哪套。
            clearFocusPin();
            const pin = new AMap.Marker({
              position: pos,
              anchor: 'bottom-center',
              // 让圆点中心正好落在房源坐标上（底边下移一个圆点半径）
              offset: new AMap.Pixel(0, 8),
              zIndex: 500,
              content:
                '<div class="map-focus-pin">' +
                '  <div class="mfp-price">' + formatPrice(house.price) + '万</div>' +
                '  <div class="mfp-dot"><span class="mfp-ring"></span></div>' +
                '</div>',
            });
            window._mapInstance.add(pin);
            window._mapFocusPin = pin;
            hideNearbyLabels(pos);

            // 显示信息窗体（锚在标点上方，避免压住价格标签）
            showHouseInfo(house, pin);
          }
        }
        
        function viewHouseFromMap(house) {
          // 从地图点击查看详情（同时添加历史）
          viewHouse(house);
        }

        /** 详情页：记录进入前的 tab，返回时原路退回 */
        const prevTab = ref('home');

        /** 详情页派生指标：单价与月供（与对比页口径一致：3 成首付 / 30 年 / 3.85%） */
        const detailMetrics = computed(() => {
          const h = detailHouse.value;
          if (!h) return { perUnit: null, monthly: null, downPayment: null, loan: null };
          const price = Number(String(h.price || '').replace(/[^\d.]/g, ''));
          const area = Number(String(h.area || '').replace(/[^\d.]/g, ''));
          const perUnit = price && area ? (price / area).toFixed(2) : null;
          let monthly = null, downPayment = null, loanTotal = null;
          if (price) {
            const loan = price * 10000 * 0.7;
            const r = 0.0385 / 12;
            const n = 360;
            monthly = (loan * r * Math.pow(1 + r, n) / (Math.pow(1 + r, n) - 1) / 10000).toFixed(2);
            downPayment = (price * 0.3).toFixed(1);
            loanTotal = (price * 0.7).toFixed(1);
          }
          return { perUnit, monthly, downPayment, loan: loanTotal };
        });

        /** 同区域其他房源：用已加载的列表拼，不额外请求 */
        const similarHouses = computed(() => {
          const h = detailHouse.value;
          if (!h) return [];
          const pool = [].concat(houses.value || [], hotHouses.value || [], recHouses.value || []);
          const seen = new Set([h.id]);
          const out = [];
          for (const item of pool) {
            if (!item || item.region !== h.region || seen.has(item.id)) continue;
            seen.add(item.id);
            out.push(item);
            if (out.length >= 3) break;
          }
          return out;
        });

        /** 联系业主：复制电话，不弹第三方对话框（真实数据，没有就如实说明） */
        function contactOwner() {
          const h = detailHouse.value;
          if (!h) return;
          const name = h.contact_name || '业主';
          const phone = h.contact_phone || '';
          if (!phone) {
            alert('该房源暂未留联系方式，可先收藏，后续再看');
            return;
          }
          try {
            navigator.clipboard.writeText(phone);
            alert('已复制' + name + '的电话：' + phone);
          } catch (e) {
            alert(name + ' 的电话：' + phone);
          }
        }

        /** 打开房源详情页（替代原来的弹窗），同步地址栏便于刷新/分享/后退 */
        function viewHouse(house) {
          if (!house) return;
          // 换房源时旧的地图实例绑在即将被销毁的 DOM 上，先清理
          destroyNearbyMap();
          if (tab.value !== 'detail') prevTab.value = tab.value;
          detailHouse.value = house;
          tab.value = 'detail';
          window.scrollTo(0, 0);
          try {
            window.history.pushState({ houseId: house.id }, '', '?house=' + house.id);
          } catch (e) { /* file:// 等场景忽略 */ }
          addToHistory(house);
          // 记录浏览行为：后端优先按 Token 归属用户，未登录时只累加房源浏览量
          api.get('/houses/' + house.id).catch(function() {});
          // 加载该房源的评论与口碑摘要
          loadComments(house.id);
          loadHouseSentiment(house.id);
          // 周边配套（高德周边搜索，失败只是列表为空）
          loadNearby(house.id);
        }

        // ===== 详情页「周边配套」：高德周边搜索 =====
        // 一类一色 + 一个汉字：地图上的点靠颜色和字区分设施类型，tab 用同色圆点呼应
        const NEARBY_META = [
          { key: 'traffic', label: '交通', color: '#2563eb', glyph: '交' },
          { key: 'school', label: '学校', color: '#7c3aed', glyph: '学' },
          { key: 'food', label: '餐饮', color: '#ea580c', glyph: '餐' },
          { key: 'shop', label: '购物', color: '#0891b2', glyph: '购' },
          { key: 'hospital', label: '医院', color: '#e11d48', glyph: '医' },
          { key: 'bank', label: '银行', color: '#b45309', glyph: '银' },
        ];
        const nearbyCategories = ref([]);
        const nearbyTab = ref('traffic');
        const nearbyLoading = ref(false);
        // 底图切片就绪前不放行遮罩，避免先看到半张白图
        const nearbyMapReady = ref(false);

        // 固定 6 个 tab（即使某类没查到也保留，避免 tab 数量随数据跳动）
        const nearbyTabs = computed(() => NEARBY_META.map(meta => {
          const hit = nearbyCategories.value.find(c => c.key === meta.key);
          return {
            key: meta.key, label: meta.label, color: meta.color,
            items: (hit && hit.items) || [],
          };
        }));
        const nearbyItems = computed(() => {
          const hit = nearbyTabs.value.find(t => t.key === nearbyTab.value);
          return (hit && hit.items) || [];
        });
        const nearbyMeta = (key) => NEARBY_META.find(m => m.key === key) || NEARBY_META[0];

        async function loadNearby(houseId) {
          nearbyCategories.value = [];
          nearbyLoading.value = true;
          nearbyMapReady.value = false;
          await nextTick();
          // 先把地图建起来：底图切片与周边搜索并行，不用等 6 个分类查完才见到地图
          ensureNearbyMap();
          try {
            const res = await api.get('/houses/' + houseId + '/surroundings');
            const data = await res.json();
            // 请求期间用户可能已切走，避免把数据挂到别的房源上
            if (!detailHouse.value || detailHouse.value.id !== houseId) return;
            nearbyCategories.value = data.categories || [];
          } catch (e) {
            if (!detailHouse.value || detailHouse.value.id !== houseId) return;
            nearbyCategories.value = [];
          }
          if (!detailHouse.value || detailHouse.value.id !== houseId) return;
          nearbyLoading.value = false;
          const first = nearbyCategories.value.find(c => c.items && c.items.length);
          nearbyTab.value = first ? first.key : 'traffic';
          await nextTick();
          if (window._dpMap) renderNearbyPois(window._dpMap);
        }

        function formatDistance(meters) {
          const m = Number(meters) || 0;
          return m >= 1000 ? (m / 1000).toFixed(1) + ' 公里' : m + ' 米';
        }

        function destroyNearbyMap() {
          if (window._dpMap) {
            try { window._dpMap.destroy(); } catch (e) {}
            window._dpMap = null;
          }
          window._dpPoiMarkers = [];
          window._dpHouseMarker = null;
          nearbyMapReady.value = false;
        }

        /**
         * 建图：只留底图 / 路网 / 建筑，关掉高德自带的 POI 图标与文字。
         * 好处是切片少、渲染快，且地图上只剩我们自己的设施点，不会满屏抢视线。
         */
        function ensureNearbyMap() {
          const el = document.getElementById('dpMap');
          if (!el || !detailHouse.value) return false;
          if (typeof AMap === 'undefined') {
            // 用户可能没进过「地图找房」，SDK 尚未注入。这里主动拉起一次
            // （loadAmapSdk 内部幂等），否则详情页周边地图会一直停在「地图加载中」。
            loadAmapSdk();
            setTimeout(ensureNearbyMap, 200);
            return false;
          }
          if (window._dpMap) return true;
          const lng = parseFloat(detailHouse.value.longitude);
          const lat = parseFloat(detailHouse.value.latitude);
          if (isNaN(lng) || isNaN(lat)) return false;
          const map = new AMap.Map('dpMap', {
            zoom: 15,
            center: [lng, lat],
            viewMode: '2D',
            features: ['bg', 'road', 'building'],
            mapStyle: 'amap://styles/normal',
            resizeEnable: true,
          });
          window._dpMap = map;
          map.on('complete', function () {
            nearbyMapReady.value = true;
            // 周边数据可能比 SDK 先到（进详情页时 SDK 才开始异步加载），
            // 那样 loadHouseDetail 里的 renderNearbyPois 会因为地图还没建好被跳过，
            // 这里在切片就绪后补画一次，避免「有列表没标记」。
            if (nearbyCategories.value.some(c => c.items && c.items.length)) {
              renderNearbyPois(map);
            }
          });
          // 房源定位：气泡只是标注，真正的坐标点用绿点 + 脉冲圈钉在房屋位置上
          window._dpHouseMarker = new AMap.Marker({
            position: [lng, lat],
            zIndex: 300,
            content: '<div class="dp-house-anchor">'
              + '<div class="dp-house-bubble">'
              + '<svg class="ic"><use href="#i-home"></use></svg>'
              + '<span>' + (detailHouse.value.title || '本房源').slice(0, 14) + '</span>'
              + '</div>'
              + '<span class="dp-house-dot"></span>'
              + '</div>',
          });
          window._dpHouseMarker.on('click', function () {
            const h = detailHouse.value;
            if (!h) return;
            if (window._dpInfoWindow) {
              try { window._dpInfoWindow.close(); } catch (e) {}
            }
            const win = new AMap.InfoWindow({
              content: '<div class="poi-info"><b>' + (h.title || '本房源') + '</b>'
                + '<div class="poi-meta">' + (h.address || '') + (h.region ? '（' + h.region + '）' : '') + '</div>'
                + '<div class="poi-src">本房源位置 · ' + lng.toFixed(5) + ', ' + lat.toFixed(5) + '</div></div>',
              offset: new AMap.Pixel(0, -18),
            });
            win.open(map, [lng, lat]);
            window._dpInfoWindow = win;
          });
          map.add(window._dpHouseMarker);
          // 兜底：弱网下 complete 可能不触发，5 秒后也撤掉加载遮罩
          setTimeout(function () { nearbyMapReady.value = true; }, 5000);
          return true;
        }

        /** 只画当前 tab 的设施点，切换 tab 时整体重画 */
        function renderNearbyPois(map) {
          if (!map) return;
          if (window._dpPoiMarkers && window._dpPoiMarkers.length) {
            try { map.remove(window._dpPoiMarkers); } catch (e) {}
          }
          window._dpPoiMarkers = [];
          const meta = nearbyMeta(nearbyTab.value);
          nearbyItems.value.forEach(p => {
            if (!p.lng || !p.lat) return;
            const dot = new AMap.Marker({
              position: [p.lng, p.lat],
              zIndex: 150,
              content: '<div class="dp-poi-pin" style="--c:' + meta.color + '"><b>'
                + meta.glyph + '</b></div>',
            });
            dot.on('click', function () { focusNearby(p); });
            window._dpPoiMarkers.push(dot);
            map.add(dot);
          });
          // 房源 + 当前分类设施一起入镜，避免 300px 的小地图只看得到其中一半
          try {
            const all = (window._dpHouseMarker ? [window._dpHouseMarker] : []).concat(window._dpPoiMarkers);
            if (all.length > 1) map.setFitView(all, false, [28, 28, 28, 28], 16);
          } catch (e) { /* 视野自适应失败不影响打点 */ }
        }

        function setNearbyTab(key) {
          nearbyTab.value = key;
          if (window._dpMap) renderNearbyPois(window._dpMap);
        }

        function focusNearby(p) {
          const map = window._dpMap;
          if (!map || !p.lng) return;
          map.setZoomAndCenter(16, [p.lng, p.lat]);
          if (window._dpInfoWindow) {
            try { window._dpInfoWindow.close(); } catch (e) {}
          }
          const win = new AMap.InfoWindow({
            content: '<div class="poi-info"><b>' + p.name + '</b>'
              + '<div class="poi-meta">' + (p.address || p.type || '') + '</div>'
              + '<div class="poi-src">距房源 ' + formatDistance(p.distance) + ' · 来源：高德POI</div></div>',
            offset: new AMap.Pixel(0, -10),
          });
          win.open(map, [p.lng, p.lat]);
          window._dpInfoWindow = win;
        }

        /** 从详情页返回来源页 */
        function closeHouseDetail() {
          destroyNearbyMap();
          detailHouse.value = null;
          switchTab(prevTab.value && prevTab.value !== 'detail' ? prevTab.value : 'home');
          try {
            window.history.pushState({}, '', window.location.pathname);
          } catch (e) { /* ignore */ }
        }

        // 浏览器后退：从详情页退回到来源页，而不是离开站点
        window.addEventListener('popstate', function () {
          if (tab.value === 'detail') {
            destroyNearbyMap();
            detailHouse.value = null;
            tab.value = prevTab.value && prevTab.value !== 'detail' ? prevTab.value : 'home';
            window.scrollTo(0, 0);
          }
        });

        function addToHistory(house) {
          const h = { ...house, browseTime: '刚刚' };
          historyList.value = historyList.value.filter(x => x.id !== house.id);
          historyList.value.unshift(h);
          if (historyList.value.length > 50) historyList.value = historyList.value.slice(0, 50);
        }
        async function removeFromHistory(houseId) {
          historyList.value = historyList.value.filter(x => x.id !== houseId);
          if (!currentUser.value || currentUser.value.role === 'admin') return;
          try { await api.del('/houses/browse-history/' + houseId); } catch (e) {}
        }
        async function clearHistory() {
          if (!confirm('确定要清空全部浏览历史吗？')) return;
          if (!currentUser.value || currentUser.value.role === 'admin') {
            historyList.value = [];
            return;
          }
          try {
            await api.del('/houses/browse-history');
            historyList.value = [];
          } catch(e) {
            alert('清空失败，请稍后重试');
          }
        }

        async function loadFavorites() {
          // 收藏接口要求普通用户登录态，未登录 / 管理员不请求，避免 401 / 403
          if (!currentUser.value || currentUser.value.role === 'admin') {
            favHouses.value = [];
            return;
          }
          try {
            const res = await api.get('/houses/favorites');
            const data = await res.json();
            favHouses.value = data.houses || data.favorites || [];
          } catch(e) { console.error(e); }
        }

        function isFavorited(id) {
          return favHouses.value.some(h => h.id === id);
        }

        async function toggleFavorite(house) {
          if (!currentUser.value) {
            loginError.value = '登录后才能收藏房源';
            showLoginModal.value = true;
            return;
          }
          if (currentUser.value.role === 'admin') {
            alert('管理员账号不参与收藏，请使用普通用户账号体验个性化推荐');
            return;
          }
          try {
            if (isFavorited(house.id)) {
              await api.del('/houses/favorites/' + house.id);
              favHouses.value = favHouses.value.filter(h => h.id !== house.id);
            } else {
              // user_id 由后端从 Token 解析，前端不再传递，避免行为记到别人名下
              await api.post('/houses/favorites', { house_id: house.id });
              favHouses.value.unshift(house);
            }
            loadRecommend();
          } catch(e) { console.error(e); }
        }

        const compareVerdict = ref(null);
        const compareLoading = ref(false);

        async function loadCompare() {
          // 对比按登录用户名隔离，未登录不请求
          if (!currentUser.value) {
            compareHouses.value = [];
            compareVerdict.value = null;
            return;
          }
          compareLoading.value = true;
          try {
            const res = await api.get('/houses/compare');
            const data = await res.json();
            compareHouses.value = data.items || data.houses || data.compare || [];
            compareVerdict.value = data.verdict || null;
          } catch(e) {
            console.error(e);
            compareVerdict.value = null;
          }
          compareLoading.value = false;
        }

        // 对比表行定义：只保留能影响决策的字段（业主电话、发布时间这类不进表）
        const COMPARE_SECTIONS = [
          { title: '决策指标', rows: [
            { label: '总价', numKey: 'price_wan', better: 'min', strong: true,
              fmt: h => h.price || (h.price_wan ? h.price_wan + ' 万' : '-') },
            { label: '单价', hint: '万/㎡ · 越低越划算', numKey: 'per_unit', better: 'min', strong: true,
              fmt: h => (h.per_unit ? h.per_unit.toFixed(2) : '-') },
            { label: '月供', hint: '3 成首付 · 30 年 · 3.85%', numKey: 'monthly_payment', better: 'min',
              fmt: h => (h.monthly_payment ? h.monthly_payment.toFixed(2) + ' 万' : '-') },
            { label: '面积', numKey: 'area_sqm', better: 'max',
              fmt: h => (h.area_sqm ? h.area_sqm + ' ㎡' : (h.area || '-')) },
            { label: '户型', fmt: h => h.rooms || '-' },
            { label: '朝向', fmt: h => h.direction || '-' },
          ]},
          { title: '房屋属性', rows: [
            { label: '楼层', fmt: h => h.floor || '-' },
            { label: '装修', fmt: h => h.decoration || '-' },
            { label: '建成年份', numKey: 'build_year_num', better: 'max', fmt: h => h.build_year || '-' },
            { label: '房屋类型', fmt: h => h.house_type || h.rent_type || '-' },
            { label: '区域', fmt: h => Array.from(new Set([h.region, h.block].filter(Boolean))).join(' · ') || '-' },
            { label: '小区地址', fmt: h => h.address || '-' },
          ]},
          { title: '配套与描述', rows: [
            { label: '交通', long: true, fmt: h => h.traffic || '-' },
            { label: '配套', long: true, fmt: h => h.facilities || '-' },
            { label: '优势', long: true, fmt: h => h.highlights || '-' },
            { label: '周边', long: true, fmt: h => h.matching || '-' },
            { label: '描述', long: true, fmt: h => h.description || '-' },
          ]},
        ];

        /**
         * 生成对比表数据：相同值合并、数值行标最优。
         * 合并规则：N 套该字段完全一致且有值时，收进 sec.merged，表格里不再占一行。
         */
        const compareSections = computed(() => {
          const hs = compareHouses.value || [];
          if (hs.length < 2) return [];
          return COMPARE_SECTIONS.map(sec => {
            const all = sec.rows.map(r => {
              const values = hs.map(h => r.fmt(h));
              const same = values.every(v => v === values[0]);
              let bestId = null;
              if (r.numKey && r.better) {
                const nums = hs.filter(h => h[r.numKey] !== null && h[r.numKey] !== undefined && Number.isFinite(Number(h[r.numKey])));
                const distinct = new Set(nums.map(h => Number(h[r.numKey])));
                if (nums.length > 1 && distinct.size > 1) {
                  const pick = nums.reduce((a, b) => (r.better === 'min'
                    ? (Number(b[r.numKey]) < Number(a[r.numKey]) ? b : a)
                    : (Number(b[r.numKey]) > Number(a[r.numKey]) ? b : a)));
                  bestId = pick.id;
                }
              }
              return Object.assign({}, r, { values, same, bestId });
            });
            return {
              title: sec.title,
              rows: all.filter(r => !r.same),
              merged: all.filter(r => r.same && r.values[0] && r.values[0] !== '-')
                          .map(r => r.label + ' ' + r.values[0]),
              allSame: all.every(r => r.same),
            };
          });
        });

        function isCompared(id) {
          return compareHouses.value.some(h => h.id === id);
        }

        async function toggleCompare(house) {
          if (!currentUser.value) {
            loginError.value = '登录后才能对比房源';
            showLoginModal.value = true;
            return;
          }
          try {
            if (isCompared(house.id)) {
              await api.del('/houses/compare/' + house.id);
            } else {
              // user_name 由后端从 Token 解析
              const addRes = await api.post('/houses/compare', { house_id: house.id });
              const addData = await addRes.json();
              if (addData && addData.success === false) {
                alert(addData.message || '最多只能对比 4 套');
                return;
              }
            }
            // 重新拉取，拿到后端算好的单价/月供/相对均价与结论
            await loadCompare();
            loadRecommend();
          } catch(e) { console.error(e); }
        }

        async function loadHistory() {
          // 浏览足迹按普通用户隔离，管理员 / 未登录不请求
          if (!currentUser.value || currentUser.value.role === 'admin') {
            historyList.value = [];
            return;
          }
          try {
            const res = await api.get('/houses/browse-history');
            const data = await res.json();
            historyList.value = data.history || [];
          } catch(e) { console.error(e); }
        }

        // ===== 房源评论 =====
        const COMMENT_PAGE_SIZE = 10;
        const comments = ref([]);
        const commentTotal = ref(0);
        const commentAvg = ref(0);
        const commentPage = ref(1);
        const commentText = ref('');
        const commentScore = ref(5);
        const commentLoading = ref(false);
        const commentLoadingMore = ref(false);
        const commentError = ref('');
        // 楼中楼回复：当前展开回复框的评论 ID / 被回复人昵称 / 被回复的那条回复 ID
        const replyTargetId = ref(null);
        const replyToName = ref('');
        const replyTargetReplyId = ref(null);
        const replyText = ref('');
        const replyLoading = ref(false);
        const replyError = ref('');
        // 详情页口碑摘要（NLP 情感分析结果：极性分布 + 维度归因）
        const houseSenti = ref(null);
        const houseSentiLoading = ref(false);

        function formatCommentTime(v) {
          if (!v) return '';
          return String(v).replace('T', ' ').slice(0, 16);
        }

        /** 是否为当前登录用户发表的评价（接口返回该房源全部用户的评价） */
        function isMyComment(c) {
          const u = currentUser.value;
          return !!(u && u.id && c && Number(c.user_id) === Number(u.id));
        }

        /** 评论头像文字：user13 -> 13，其他昵称取首字 */
        function commentAvatar(c) {
          const n = String((c && c.user_name) || '匿名用户');
          const m = n.match(/user\s*(\d+)/i);
          return m ? m[1].slice(-2) : n.slice(0, 1).toUpperCase();
        }

        /** 评论头像配色：按用户 id 散列，避免一屏全是同色同字 */
        function commentAvatarStyle(c) {
          const hue = ((Number(c && c.user_id) || 0) * 47) % 360;
          return {
            background: 'hsl(' + hue + ', 62%, 94%)',
            color: 'hsl(' + hue + ', 42%, 38%)',
          };
        }

        /** 拉取某页评价：房源维度，不按当前用户过滤 */
        async function fetchCommentPage(houseId, page) {
          const res = await api.get(
            '/houses/' + houseId + '/comments?page=' + page + '&page_size=' + COMMENT_PAGE_SIZE
          );
          if (!res.ok) return null;
          return res.json();
        }

        async function loadComments(houseId) {
          comments.value = [];
          commentTotal.value = 0;
          commentAvg.value = 0;
          commentPage.value = 1;
          cancelReply();
          if (!houseId) return;
          try {
            const data = await fetchCommentPage(houseId, 1);
            if (!data) return;
            comments.value = data.comments || [];
            commentTotal.value = data.total || 0;
            commentAvg.value = data.avg_score || 0;
          } catch(e) { console.error(e); }
        }

        /** 加载该房源的口碑摘要（情感极性 + 好评/差评维度） */
        async function loadHouseSentiment(houseId) {
          houseSenti.value = null;
          if (!houseId) return;
          houseSentiLoading.value = true;
          try {
            const res = await api.get('/analysis/sentiment/house/' + houseId);
            if (res.ok) {
              const data = await res.json();
              if (data && data.status === 'ok' && data.total > 0) houseSenti.value = data;
            }
          } catch(e) { console.error(e); }
          houseSentiLoading.value = false;
        }

        /** 加载更多评价（继续分页，保留已加载内容） */
        async function loadMoreComments() {
          const houseId = detailHouse.value && detailHouse.value.id;
          if (!houseId || commentLoadingMore.value) return;
          if (comments.value.length >= commentTotal.value) return;
          commentLoadingMore.value = true;
          try {
            const next = commentPage.value + 1;
            const data = await fetchCommentPage(houseId, next);
            if (data) {
              const known = new Set(comments.value.map(c => c.id));
              comments.value = comments.value.concat((data.comments || []).filter(c => !known.has(c.id)));
              commentTotal.value = data.total || commentTotal.value;
              commentAvg.value = data.avg_score || commentAvg.value;
              commentPage.value = next;
            }
          } catch(e) { console.error(e); }
          commentLoadingMore.value = false;
        }

        async function submitComment() {
          if (!currentUser.value) {
            commentError.value = '登录后才能发表评价';
            showLoginModal.value = true;
            return;
          }
          if (currentUser.value.role === 'admin') {
            commentError.value = '管理员账号不参与评价，请使用普通用户账号';
            return;
          }
          const text = (commentText.value || '').trim();
          if (!text) {
            commentError.value = '请先写点评价内容';
            return;
          }
          if (!detailHouse.value || !detailHouse.value.id) return;
          commentLoading.value = true;
          commentError.value = '';
          try {
            const res = await api.post('/houses/' + detailHouse.value.id + '/comments', {
              score: commentScore.value,
              content: text,
            });
            const data = await res.json();
            if (res.ok) {
              commentText.value = '';
              commentScore.value = 5;
              await loadComments(detailHouse.value.id);
              loadHouseSentiment(detailHouse.value.id);
              // 评论会作为行为数据参与推荐，刷新一次画像
              loadRecommend();
            } else {
              commentError.value = data.detail || '提交失败，请稍后重试';
            }
          } catch(e) {
            commentError.value = '网络错误，请稍后重试';
          }
          commentLoading.value = false;
        }

        /** 删除自己的评价：前端先判定归属，后端还会按 Token 再校验一次 */
        async function deleteMyComment(c) {
          if (!c || !c.id || !detailHouse.value || !detailHouse.value.id) return;
          if (!isMyComment(c)) {
            commentError.value = '只能删除自己发表的评价';
            return;
          }
          if (!window.confirm('确定删除这条评价？删除后不可恢复。')) return;
          commentError.value = '';
          try {
            const res = await api.del('/houses/' + detailHouse.value.id + '/comments/' + c.id);
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
              if (replyTargetId.value === c.id) cancelReply();
              await loadComments(detailHouse.value.id);
              loadHouseSentiment(detailHouse.value.id);
              // 评论也是推荐的行为输入，删掉后刷新一次画像
              loadRecommend();
            } else {
              commentError.value = data.detail || '删除失败，请稍后重试';
            }
          } catch(e) {
            commentError.value = '网络错误，请稍后重试';
          }
        }

        // ===== 楼中楼回复 =====
        /** 展开 / 收起某条评论的回复框；reply 传入表示回复该楼层里的某个人 */
        function toggleReply(comment, reply) {
          if (!currentUser.value) {
            commentError.value = '登录后才能回复评价';
            showLoginModal.value = true;
            return;
          }
          if (currentUser.value.role === 'admin') {
            commentError.value = '管理员账号不参与评价互动，请使用普通用户账号';
            return;
          }
          const cid = Number(comment && comment.id);
          const rid = reply && reply.id ? Number(reply.id) : null;
          if (replyTargetId.value === cid && replyTargetReplyId.value === rid) {
            cancelReply();
            return;
          }
          replyTargetId.value = cid;
          replyTargetReplyId.value = rid;
          replyToName.value = (reply && reply.user_name) || '';
          replyText.value = '';
          replyError.value = '';
        }

        function cancelReply() {
          replyTargetId.value = null;
          replyTargetReplyId.value = null;
          replyToName.value = '';
          replyText.value = '';
          replyError.value = '';
        }

        /** 提交回复：回复统一挂在评论下，reply_to / reply_to_id 记录被回复对象 */
        async function submitReply(comment) {
          const text = (replyText.value || '').trim();
          if (!text) {
            replyError.value = '请先写点回复内容';
            return;
          }
          const houseId = detailHouse.value && detailHouse.value.id;
          if (!houseId || !comment || !comment.id) return;
          replyLoading.value = true;
          replyError.value = '';
          try {
            const res = await api.post(
              '/houses/' + houseId + '/comments/' + comment.id + '/replies',
              {
                content: text,
                reply_to: replyToName.value || '',
                reply_to_id: replyTargetReplyId.value || null,
              }
            );
            const data = await res.json();
            if (res.ok) {
              cancelReply();
              await refreshComments(houseId);
            } else {
              replyError.value = data.detail || '回复失败，请稍后重试';
            }
          } catch(e) {
            replyError.value = '网络错误，请稍后重试';
          }
          replyLoading.value = false;
        }

        /** 按已加载页数重新拉取，保留「查看全部」的翻页进度 */
        async function refreshComments(houseId) {
          const lastPage = Math.max(commentPage.value, 1);
          const merged = [];
          for (let p = 1; p <= lastPage; p++) {
            const data = await fetchCommentPage(houseId, p);
            if (!data) break;
            const items = data.comments || [];
            const known = new Set(merged.map(c => c.id));
            merged.push(...items.filter(c => !known.has(c.id)));
            commentTotal.value = data.total || 0;
            commentAvg.value = data.avg_score || 0;
          }
          comments.value = merged;
        }

        async function sendChat(question) {
          const q = question || chatInput.value.trim();
          if (!q || chatLoading.value) return;
          messages.value.push({ role: 'user', content: q });
          chatInput.value = '';
          chatLoading.value = true;
          await nextTick();
          if (chatBodyRef.value) {
            chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight;
          }
          // 先插入一条空的 AI 消息，流式追加内容
          messages.value.push({ role: 'ai', content: '', hint: '', streaming: true });
          const msgIdx = messages.value.length - 1;
          await nextTick();
          if (chatBodyRef.value) {
            chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight;
          }
          try {
            // 带上会话 ID，后端才会把本轮接到既有上下文后面（首轮为空 → 后端新建并回传）
            const payload = { question: q };
            if (chatCid.value) payload.conversation_id = chatCid.value;
            const res = await fetch(API_BASE + '/chat/stream', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            });
            if (!res.ok) throw new Error('请求失败: ' + res.status);
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop();
              for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const jsonStr = line.slice(6).trim();
                if (!jsonStr) continue;
                try {
                  const evt = JSON.parse(jsonStr);
                  // 后端在 start / done 事件里都回传 conversation_id，收到就存下来，
                  // 下一轮追问才会接上同一个 thread（否则每轮都是新会话）
                  if (evt.conversation_id && evt.conversation_id !== chatCid.value) {
                    chatCid.value = evt.conversation_id;
                    try { sessionStorage.setItem(CHAT_CID_KEY, evt.conversation_id); } catch (e) {}
                  }
                  if (evt.type === 'start' && evt.sources && evt.sources.length > 0) {
                    messages.value[msgIdx].hint = '参考来源：' + evt.sources.slice(0, 3).map(s => s.title).join('、');
                  } else if (evt.type === 'agent_step') {
                    // 多智能体调度可视化：告诉用户这次由哪个专家接手
                    messages.value[msgIdx].hint = '智能体调度：' + (evt.label || evt.agent || '') +
                      (evt.reason ? '（' + evt.reason + '）' : '');
                  } else if (evt.type === 'delta') {
                    messages.value[msgIdx].content += evt.content;
                    if (chatBodyRef.value) {
                      chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight;
                    }
                  } else if (evt.type === 'done') {
                    messages.value[msgIdx].streaming = false;
                    // 本轮召回房源里带经纬度的，挂到这条回答下面（点击标记直接进详情）
                    const located = (evt.related_houses || [])
                      .filter(h => h.longitude && h.latitude);
                    // 外部地理点（高德）：外地城市回答时用来证明「哪些片区是真的」，不是房源
                    const pois = (evt.external_pois || []).filter(p => p.lng && p.lat);
                    messages.value[msgIdx].mapHouses = located;
                    messages.value[msgIdx].externalPois = pois;
                    if (located.length || pois.length) {
                      nextTick(() => {
                        renderChatMap(msgIdx);
                        if (chatBodyRef.value) {
                          chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight;
                        }
                      });
                    }
                  }
                } catch(e) {}
              }
            }
            messages.value[msgIdx].streaming = false;
            if (!messages.value[msgIdx].content) {
              messages.value[msgIdx].content = '抱歉，暂时无法回答您的问题。';
            }
          } catch(e) {
            messages.value[msgIdx].streaming = false;
            messages.value[msgIdx].content = '抱歉，出了点小问题：' + e.message;
            messages.value[msgIdx].hint = '请检查网络连接和后端服务';
          }
          chatLoading.value = false;
          await nextTick();
          if (chatBodyRef.value) {
            chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight;
          }
        }

        /** 开新会话：清掉会话 ID 与消息列表，后端下一轮会新建 thread（老上下文不再参与） */
        function newChat() {
          if (chatLoading.value) return;
          chatCid.value = '';
          try { sessionStorage.removeItem(CHAT_CID_KEY); } catch (e) {}
          messages.value = [{ role: 'ai', content: CHAT_WELCOME }];
          chatInput.value = '';
        }

        const askPrice = ref('');
        const askResult = ref(null);
        const estError = ref('');

        // 特征贡献合计，用于展示「基准价 + 逐项调整 = 估值」
        const estFactorsTotal = computed(() => {
          const d = estData.value;
          if (!d || !d.factors_detail) return 0;
          return d.factors_detail.reduce((sum, f) => sum + (Number(f.amount) || 0), 0);
        });

        /** 房况贡献图：只画有实际金额变化的项，零影响项不占版面 */
        function renderEstimateFactors() {
          const d = estData.value;
          const items = ((d && d.factors_detail) || [])
            .filter(f => Math.round(Number(f.amount) || 0) !== 0);
          const el = document.getElementById('estimateFactorsChart');
          if (!el) return;
          if (!items.length) { el.style.display = 'none'; return; }
          el.style.display = 'block';
          renderChart('estimateFactorsChart', {
            grid: { left: 64, right: 76, top: 10, bottom: 24 },
            tooltip: {
              trigger: 'axis', axisPointer: { type: 'shadow' },
              formatter: function (p) {
                const it = items[p[0].dataIndex];
                const sign = it.amount > 0 ? '+' : '';
                return it.label + '：' + it.note + '<br/>' + sign + it.pct + '%　' + sign + it.amount + ' 万';
              },
            },
            xAxis: {
              type: 'value',
              axisLabel: { formatter: '{value} 万', color: '#8a9691' },
              splitLine: { lineStyle: { type: 'dashed', color: '#eef2f0' } },
            },
            yAxis: {
              type: 'category',
              data: items.map(i => i.label).reverse(),
              axisLine: { show: false }, axisTick: { show: false },
              axisLabel: { color: '#4a5651' },
            },
            series: [{
              type: 'bar', barWidth: '46%', barMinHeight: 2,
              data: items.map(i => ({
                value: i.amount,
                itemStyle: { color: i.amount > 0 ? '#00ae66' : '#f97316', borderRadius: [0, 4, 4, 0] },
              })).reverse(),
              label: {
                show: true, position: 'right', color: '#4a5651', fontSize: 12,
                formatter: function (p) { return (p.value > 0 ? '+' : '') + p.value + ' 万'; },
              },
            }],
          });
        }

        /** 挂牌价反查：把估值变成还价依据 */
        function checkAskPrice() {
          const ask = Number(askPrice.value);
          const d = estData.value;
          if (!ask || !d || !d.estimated_price_avg) return;
          const avg = Number(d.estimated_price_avg);
          const lo = Number(d.estimated_price_min || avg);
          const hi = Number(d.estimated_price_max || avg);
          const pct = Math.round((ask - avg) / avg * 1000) / 10;
          if (Math.abs(pct) <= 3) {
            askResult.value = {
              level: 'fair', title: '报价基本合理',
              text: ask + ' 万与估值 ' + avg + ' 万相差 ' + Math.abs(pct) + '%，处在正常议价区间内。',
            };
          } else if (pct > 3) {
            askResult.value = {
              level: 'high', title: '高估 ' + pct + '%',
              text: '比估值中枢高 ' + Math.round(ask - avg) + ' 万；若对方坚持，可拿估值上限 ' + hi + ' 万作为还价参照。',
            };
          } else {
            askResult.value = {
              level: 'low', title: '低于估值 ' + Math.abs(pct) + '%',
              text: '比估值中枢低 ' + Math.round(avg - ask) + ' 万（估值下限 ' + lo + ' 万），先核实抵押、产权年限与房屋瑕疵。',
            };
          }
        }

        async function doEstimate() {
          try {
            estData.value = null;
            mlData.value = null;
            mlError.value = '';
            estError.value = '';
            askResult.value = null;
            estLoading.value = true;
            // 并行调用：规则估算 + 机器学习预测
            const [res, mlRes] = await Promise.all([
              api.post('/houses/estimate-price', {
                  region: estForm.value.region,
                  area: estForm.value.area,
                  rooms: estForm.value.rooms,
                  decoration: estForm.value.decoration,
                  direction: estForm.value.direction || '南北',
                  floor: estForm.value.floor || '中楼层',
                }),
              api.post('/analysis/ml-predict', {
                  region: estForm.value.region,
                  area: Number(estForm.value.area) || 80,
                  rooms: estForm.value.rooms,
                  direction: estForm.value.direction || '南北',
                  rent_type: '住宅',
                }),
            ]);
            const data = await res.json();
            const ml = await mlRes.json();
            if (ml.status === 'ok' && ml.predicted_price) {
              mlData.value = ml;
            } else {
              mlError.value = (ml && ml.error) || '模型训练中，请稍后再试';
            }
            if (data && data.estimated_price_avg) {
              estData.value = data;
              pushEstHistory(data);
              await nextTick();
              renderEstimateFactors();
              // 结果在两栏下方，估完自动滚过去，不用用户自己找
              const el = document.querySelector('.estimate-result');
              if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            } else {
              // 失败就明确报错，不再渲染一张"0~0 万"的结果卡
              estError.value = (data && (data.detail || data.advice)) || '估算失败，请检查输入';
            }
          } catch(e) {
            estError.value = '网络错误，请稍后重试';
          }
          estLoading.value = false;
        }

        // ===== 估价页：区域行情 / 最近估价 / 典型房源 =====
        const marketOverview = ref(null);
        const estHistory = ref([]);

        /** 区域行情（复用大屏的聚合结果，取完缓存，切页不重复请求） */
        async function loadMarketOverview() {
          if (marketOverview.value) return;
          try {
            const res = await api.get('/analysis/dashboard');
            const d = await res.json();
            if (d.status === 'error') return;
            marketOverview.value = {
              overview: d.overview || {},
              regionAvg: d.region_avg_price || [],
              regionCount: d.region_count || [],
            };
          } catch (e) { console.error(e); }
        }

        const estRegionStat = computed(() => {
          const m = marketOverview.value;
          if (!m) return null;
          const region = estForm.value.region;
          const avg = (m.regionAvg.find(x => x.name === region) || {}).value || 0;
          const cnt = (m.regionCount.find(x => x.name === region) || {}).value || 0;
          const cityAvg = Number(m.overview.avg_price) || 0;
          const total = Number(m.overview.total_houses) || 0;
          if (!avg) return null;
          const diffPct = cityAvg ? Math.round((avg - cityAvg) / cityAvg * 1000) / 10 : 0;
          return {
            region,
            avg: Math.round(avg),
            count: cnt,
            cityAvg: Math.round(cityAvg),
            diffPct,
            share: total ? Math.round(cnt / total * 1000) / 10 : 0,
          };
        });

        const estRegionRank = computed(() => {
          const m = marketOverview.value;
          if (!m || !m.regionAvg.length) return [];
          const list = m.regionAvg.slice().sort((a, b) => b.value - a.value);
          const max = list[0].value || 1;
          return list.map(x => ({
            name: x.name,
            value: Math.round(x.value),
            pct: Math.max(8, Math.round(x.value / max * 100)),
          }));
        });

        function loadEstHistory() {
          try {
            const raw = JSON.parse(localStorage.getItem('est_history') || '[]');
            estHistory.value = Array.isArray(raw) ? raw : [];
          } catch (e) { estHistory.value = []; }
        }

        /** 估价成功后写入本地历史（同区域同面积同户型只保留最新一条） */
        function pushEstHistory(d) {
          const f = estForm.value;
          const item = {
            region: f.region,
            area: f.area,
            rooms: f.rooms,
            direction: f.direction,
            floor: f.floor,
            decoration: f.decoration,
            avg: Math.round(d.estimated_price_avg || 0),
            lo: Math.round(d.estimated_price_min || 0),
            hi: Math.round(d.estimated_price_max || 0),
            ts: Date.now(),
          };
          const list = estHistory.value.filter(x => !(
            x.region === item.region && String(x.area) === String(item.area) && x.rooms === item.rooms
          ));
          list.unshift(item);
          estHistory.value = list.slice(0, 5);
          localStorage.setItem('est_history', JSON.stringify(estHistory.value));
        }

        function clearEstHistory() {
          estHistory.value = [];
          localStorage.removeItem('est_history');
        }

        function restoreEstimate(item) {
          estForm.value = {
            region: item.region,
            area: item.area,
            rooms: item.rooms,
            direction: item.direction || '南北',
            floor: item.floor || '中楼层',
            decoration: item.decoration || '精装',
          };
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        const EST_SAMPLES = {
          '刚需两居': { region: '朝阳区', area: 89, rooms: '2室1厅', direction: '南北', floor: '中楼层', decoration: '精装' },
          '改善三居': { region: '海淀区', area: 128, rooms: '3室2厅', direction: '南北', floor: '高楼层', decoration: '精装' },
          '小户型': { region: '丰台区', area: 55, rooms: '1室1厅', direction: '南', floor: '中楼层', decoration: '简装' },
        };

        function applyEstSample(name) {
          const s = EST_SAMPLES[name];
          if (!s) return;
          estForm.value = Object.assign({}, s);
          doEstimate();
        }

        // ============ 模块 C：数据可视化大屏 ============
        const DASH_COLORS = ['#00ae66', '#ff7a00', '#3b82f6', '#8b5cf6', '#f43f5e', '#f59e0b', '#06b6d4', '#84cc16'];

        // 统一图表视觉：字体、轴线、网格线、tooltip 与全站卡片风格保持一致
        // （用 ECharts 主题统一收口，避免 9 张图各写一遍 axisLabel/splitLine）
        const DASH_THEME = {
          color: DASH_COLORS,
          textStyle: {
            fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif",
            fontSize: 11, color: '#5d6b65',
          },
          categoryAxis: {
            axisLine: { lineStyle: { color: '#e6ebe8' } },
            axisTick: { show: false },
            axisLabel: { color: '#8a938e', fontSize: 10 },
            splitLine: { show: false },
            splitArea: { show: false },
          },
          valueAxis: {
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: '#8a938e', fontSize: 10 },
            splitLine: { lineStyle: { color: '#f0f3f1', type: 'dashed' } },
          },
          tooltip: {
            backgroundColor: 'rgba(255,255,255,.98)', borderColor: '#e6ebe8', borderWidth: 1,
            padding: [8, 10], textStyle: { color: '#22332b', fontSize: 12 },
            extraCssText: 'box-shadow: 0 8px 24px rgba(16,42,32,.10); border-radius: 8px;',
          },
          legend: { textStyle: { color: '#8a938e', fontSize: 10 }, itemWidth: 10, itemHeight: 8 },
        };
        if (typeof echarts !== 'undefined' && echarts.registerTheme) {
          echarts.registerTheme('houseLight', DASH_THEME);
        }

        function renderChart(elId, option) {
          if (typeof echarts === 'undefined') return null;
          const el = document.getElementById(elId);
          if (!el) return null;
          if (chartInstances[elId]) { chartInstances[elId].dispose(); delete chartInstances[elId]; }
          const chart = echarts.init(el, 'houseLight');
          chart.setOption(option);
          chartInstances[elId] = chart;
          return chart;
        }

        async function loadDashboard() {
          dashLoading.value = true;
          try {
            const res = await api.get('/analysis/dashboard');
            const data = await res.json();
            if (data.status === 'error') throw new Error(data.error);
            dashData.value = data;
            dashLoading.value = false;
            await nextTick();
            // 区域对比：均价 + 最新月度环比，这里是全平台唯一的区域横向对比入口
            const momMap = {};
            (data.region_mom || []).forEach(x => { momMap[x.name] = x.mom; });
            renderChart('chartRegion', {
                grid: { left: 108, right: 58, top: 10, bottom: 25 },
                tooltip: {
                  trigger: 'axis', axisPointer: { type: 'shadow' },
                  formatter: (params) => {
                    const p = params[0];
                    const mom = momMap[p.name];
                    const momText = mom === undefined ? '暂无环比'
                      : (mom >= 0 ? '环比 ↑' : '环比 ↓') + Math.abs(mom) + '%';
                    return p.name + '<br/>平均总价 ' + p.value + ' 万<br/>' + momText;
                  },
                },
                xAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed' } } },
                yAxis: {
                  type: 'category',
                  data: data.region_avg_price.map(x => x.name).reverse(),
                  axisLabel: {
                    fontSize: 11,
                    formatter: (name) => {
                      const mom = momMap[name];
                      if (mom === undefined) return name;
                      const arrow = mom >= 0 ? '↑' : '↓';
                      return name + ' ' + arrow + Math.abs(mom) + '%';
                    },
                  },
                },
                series: [{
                  type: 'bar', data: data.region_avg_price.map(x => x.value).reverse(),
                  itemStyle: {
                    color: (p) => {
                      const region = data.region_avg_price.map(x => x.name).reverse()[p.dataIndex];
                      const mom = momMap[region];
                      if (mom === undefined) return '#9fd9bd';
                      return mom >= 0 ? '#00ae66' : '#f43f5e';
                    },
                    borderRadius: [0, 4, 4, 0],
                  },
                  label: { show: true, position: 'right', formatter: '{c} 万', fontSize: 11 },
                  barWidth: '55%',
                }],
              });
              // 气泡图：X=区域均价，Y=在挂套数，气泡大小反映挂牌量（看"贵且多"的区）
              const scatterPts = data.region_scatter || [];
              const maxCnt = Math.max(1, ...scatterPts.map(p => p.value[1]));
              renderChart('chartScatter', {
                grid: { left: 52, right: 28, top: 34, bottom: 42 },
                tooltip: {
                  formatter: (p) => p.name + '<br/>均价 ' + p.value[0] + ' 万'
                    + '<br/>在挂 ' + p.value[1] + ' 套'
                    + '<br/>平均面积 ' + p.value[2] + ' ㎡',
                },
                xAxis: {
                  type: 'value', name: '均价(万)', nameLocation: 'middle', nameGap: 26,
                  nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 },
                  splitLine: { lineStyle: { type: 'dashed' } },
                },
                yAxis: {
                  type: 'value', name: '在挂套数', nameTextStyle: { fontSize: 10 },
                  axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { type: 'dashed' } },
                },
                series: [{
                  type: 'scatter', data: scatterPts,
                  symbolSize: (v) => 10 + 26 * Math.sqrt(v[1] / maxCnt),
                  itemStyle: { color: 'rgba(0,174,102,.75)', borderColor: '#fff', borderWidth: 1 },
                  // 只给挂牌量大的区标名字，其余靠 tooltip；右上方几个区挤在一起时纵向错开
                  label: {
                    show: true, position: 'right', distance: 5, fontSize: 9, color: '#666',
                    formatter: (p) => p.value[1] >= 2500 ? p.name : '',
                  },
                  labelLayout: { moveOverlap: 'shiftY' },
                }],
              });
              // 热力图：区域 × 价格段，颜色深浅=该区落在该价位段的房源数
              const hm = data.region_price_heatmap || { x: [], y: [], data: [] };
              const maxCell = Math.max(1, ...hm.data.map(d => d[2]));
              renderChart('chartHeat', {
                grid: { left: 62, right: 44, top: 12, bottom: 38 },
                tooltip: {
                  formatter: (p) => (hm.y[p.value[1]] || '') + ' · ' + (hm.x[p.value[0]] || '')
                    + '<br/>' + p.value[2] + ' 套',
                },
                xAxis: {
                  type: 'category', data: hm.x, axisTick: { show: false },
                  // 轴标签去掉「万」字，6 个价位段才排得下（万为单位）
                  axisLabel: { fontSize: 9, interval: 0, formatter: (v) => v.replace('万', '') },
                },
                yAxis: {
                  type: 'category', data: hm.y, axisTick: { show: false },
                  axisLabel: { fontSize: 10 },
                },
                visualMap: {
                  min: 0, max: maxCell, calculable: true, orient: 'vertical',
                  right: 0, top: 'center', itemWidth: 10, itemHeight: 72,
                  inRange: { color: ['#eafaf1', '#9fd9bd', '#00ae66', '#ff7a00'] },
                  textStyle: { fontSize: 8 },
                },
                series: [{
                  type: 'heatmap', data: hm.data,
                  label: { show: true, fontSize: 9, formatter: (p) => p.value[2] },
                  itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 3 },
                }],
              });
              renderChart('chartRoom', {
                tooltip: { trigger: 'item', formatter: '{b}: {c} 套 ({d}%)' },
                legend: { bottom: 0 },
                series: [{
                  type: 'pie', radius: ['38%', '65%'], center: ['50%', '45%'],
                  itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
                  // 小于 2% 的户型（7室/8室）不画标签，否则挤在一起看不清
                  label: { formatter: (p) => p.percent >= 3 ? p.name + '\n' + p.percent.toFixed(1) + '%' : '' },
                  data: data.room_dist.map((x, i) => ({ name: x.name, value: x.value, itemStyle: { color: DASH_COLORS[i % DASH_COLORS.length] } })),
                }],
              });
              renderChart('chartPrice', {
                grid: { left: 55, right: 20, top: 25, bottom: 30 },
                tooltip: { trigger: 'axis' },
                xAxis: { type: 'category', data: data.price_dist.map(x => x.name), axisLabel: { rotate: 25, fontSize: 10 } },
                yAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed' } } },
                series: [{
                  type: 'bar', data: data.price_dist.map(x => x.value),
                  itemStyle: { color: '#ff7a00', borderRadius: [4, 4, 0, 0] },
                  barWidth: '50%',
                }],
              });
              renderChart('chartDirection', {
                tooltip: { trigger: 'item', formatter: '{b}: {c} 套 ({d}%)' },
                legend: { bottom: 0 },
                series: [{
                  // 南丁格尔玫瑰图：半径区分大小，比普通饼图更能看出「朝南一枝独秀」
                  type: 'pie', roseType: 'radius', radius: ['18%', '66%'], center: ['50%', '44%'],
                  itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
                  label: { formatter: '{b} {d}%', fontSize: 10 },
                  data: data.direction_dist.map((x, i) => ({ name: x.name, value: x.value, itemStyle: { color: DASH_COLORS[(i + 2) % DASH_COLORS.length] } })),
                }],
              });
              // 帕累托图：柱=各面积段套数，折线=累计占比（看 80% 房源集中在哪几段）
              // 面积段按自然顺序（小→大）排列，比按套数排序更符合"面积分布"的直觉
              const AREA_ORDER = ['<60㎡', '60-90㎡', '90-120㎡', '120-160㎡', '160-200㎡', '>200㎡'];
              const areaList = data.area_dist.slice().sort(
                (a, b) => AREA_ORDER.indexOf(a.name) - AREA_ORDER.indexOf(b.name));
              const areaVals = areaList.map(x => x.value);
              const areaTotal = areaVals.reduce((a, b) => a + b, 0) || 1;
              let areaAcc = 0;
              const areaCum = areaVals.map(v => { areaAcc += v; return +(areaAcc / areaTotal * 100).toFixed(1); });
              renderChart('chartArea', {
                grid: { left: 48, right: 46, top: 32, bottom: 34 },
                tooltip: { trigger: 'axis' },
                legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
                xAxis: { type: 'category', data: areaList.map(x => x.name), axisLabel: { fontSize: 10 } },
                yAxis: [
                  { type: 'value', name: '套数', nameTextStyle: { fontSize: 10 }, splitLine: { lineStyle: { type: 'dashed' } } },
                  { type: 'value', name: '累计', max: 100, axisLabel: { formatter: '{value}%', fontSize: 10 }, splitLine: { show: false } },
                ],
                series: [
                  {
                    name: '套数', type: 'bar', data: areaVals, barWidth: '52%',
                    itemStyle: { color: '#3b82f6', borderRadius: [4, 4, 0, 0] },
                  },
                  {
                    name: '累计占比', type: 'line', yAxisIndex: 1, data: areaCum, smooth: true,
                    symbolSize: 6, lineStyle: { width: 2, color: '#ff7a00' }, itemStyle: { color: '#ff7a00' },
                  },
                ],
              });
              // 100% 堆叠条：每个区的户型结构占比，横向对比区域差异
              const structs = data.region_room_struct || [];
              const roomCats = data.room_categories || [];
              renderChart('chartStruct', {
                grid: { left: 62, right: 18, top: 30, bottom: 26 },
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => v + '%' },
                legend: { top: 0, itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 10 } },
                xAxis: {
                  type: 'value', max: 100, axisLabel: { formatter: '{value}%', fontSize: 10 },
                  splitLine: { lineStyle: { type: 'dashed' } },
                },
                yAxis: { type: 'category', data: structs.slice().reverse().map(x => x.name), axisLabel: { fontSize: 10 } },
                series: roomCats.map((cat, i) => ({
                  name: cat, type: 'bar', stack: 'room', barWidth: '58%',
                  itemStyle: { color: DASH_COLORS[i % DASH_COLORS.length] },
                  label: { show: true, fontSize: 9, color: '#fff', formatter: (p) => p.value >= 10 ? p.value + '%' : '' },
                  data: structs.slice().reverse().map(x => +((x.rooms[cat] || 0) / (x.total || 1) * 100).toFixed(1)),
                })),
              });
              renderChart('chartBlock', {
                grid: { left: 90, right: 30, top: 10, bottom: 25 },
                tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
                xAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed' } } },
                yAxis: { type: 'category', data: data.top_blocks.map(x => x.name).reverse() },
                series: [{
                  type: 'bar', data: data.top_blocks.map(x => x.value).reverse(),
                  itemStyle: { color: '#8b5cf6', borderRadius: [0, 4, 4, 0] },
                  label: { show: true, position: 'right', formatter: '{c} 万' },
                  barWidth: '55%',
                }],
              });
          } catch(e) {
            console.error(e);
            dashLoading.value = false;
          }
        }

        // ============ 模块 D：情感分析已并入详情页口碑摘要，不再单独加载 ============

        // ============ 模块 E：房价走势时间序列分析 ============
        // 图表默认只看近 3 年历史 + 未来 12 个月，否则 2020 年以来的历史会把「未来」压成一条缝
        const trendRange = ref('3y');
        const TREND_RANGE_MONTHS = { '3y': 36, '5y': 60, all: 0 };
        const trendRangeText = computed(() => {
          const d = trendData.value;
          if (!d || d.status !== 'ok' || !(d.history || []).length) return '';
          const keep = TREND_RANGE_MONTHS[trendRange.value] || 0;
          const hist = keep > 0 ? d.history.slice(-keep) : d.history;
          const pred = d.predictions || [];
          const now = hist[hist.length - 1].month;
          const pEnd = pred.length ? pred[pred.length - 1].month : '';
          return '历史 ' + hist[0].month + ' ~ ' + now + '（现在）· 预测至 ' + pEnd;
        });

        function setTrendRange(v) {
          if (trendRange.value === v) return;
          trendRange.value = v;
          renderTrendChart();
        }

        /** 渲染走势图：历史 → 现在（竖线标记）→ 预测（浅色底），预测区间始终在本月之后 */
        function renderTrendChart() {
          const data = trendData.value;
          if (!data || data.status !== 'ok') return;
          const pred = data.predictions || [];
          const keep = TREND_RANGE_MONTHS[trendRange.value] || 0;
          const histAll = data.history || [];
          const hist = keep > 0 ? histAll.slice(-keep) : histAll;
          const splitIdx = hist.length;
          const months = hist.map(x => x.month).concat(pred.map(x => x.month));
          const prices = hist.map(x => x.price).concat(pred.map(x => x.price));
          const nowMonth = splitIdx ? months[splitIdx - 1] : '';
          // 类目越多抽稀越狠，保证首末两个月份都能看清
          const step = Math.max(0, Math.round(months.length / 8) - 1);
          renderChart('chartTrend', {
            grid: { left: 70, right: 34, top: 42, bottom: 70 },
            tooltip: { trigger: 'axis' },
            legend: { data: ['历史均价', '预测均价'], top: 0 },
            xAxis: {
              type: 'category', data: months, boundaryGap: false,
              axisLabel: {
                rotate: 35, fontSize: 10, hideOverlap: true,
                // 首末月份必显示（末端 = 预测最后一个月），中间按步长抽稀
                interval: (idx) => idx === 0 || idx === months.length - 1 || idx % (step + 1) === 0,
              },
            },
            yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { type: 'dashed' } } },
            series: [
              {
                // 历史线只画到「现在」，未来月份留空由预测序列接管
                name: '历史均价', type: 'line',
                data: prices.map((p, i) => (i <= splitIdx - 1 ? p : null)),
                markPoint: { data: [{ type: 'max', name: '最高' }] },
                markLine: {
                  silent: true, symbol: 'none',
                  label: {
                    formatter: '现在 ' + nowMonth, position: 'insideEndTop',
                    color: '#ff7a00', fontSize: 10, rotate: 0,
                  },
                  lineStyle: { color: '#ff7a00', type: 'dashed', width: 1 },
                  data: [{ xAxis: nowMonth }],
                },
                markArea: {
                  silent: true, itemStyle: { color: 'rgba(255,122,0,0.06)' },
                  data: pred.length ? [[{ xAxis: nowMonth }, { xAxis: months[months.length - 1] }]] : [],
                },
                lineStyle: { color: '#00ae66', width: 2 },
                itemStyle: { color: '#00ae66' },
                symbol: 'circle', symbolSize: 3,
                areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(0,174,102,0.25)' }, { offset: 1, color: 'rgba(0,174,102,0)' }] } },
              },
              {
                name: '预测均价', type: 'line',
                data: months.map((_, i) => (i < splitIdx - 1 ? null : prices[i])),
                lineStyle: { color: '#ff7a00', width: 2, type: 'dashed' },
                itemStyle: { color: '#ff7a00' },
                symbol: 'diamond', symbolSize: 5,
              },
            ],
          });
        }

        async function loadTrendRegions() {
          try {
            // 区域横向对比已收归「数据分析」页，这里只需要区域列表
            const rRes = await api.get('/analysis/trend/regions');
            const regions = await rRes.json();
            trendRegions.value = regions.regions || [];
            if (trendRegions.value.length > 0 && !trendRegion.value) {
              trendRegion.value = trendRegions.value[0].name;
              loadTrend();
            } else if (trendRegion.value) {
              loadTrend();
            }
          } catch(e) { console.error(e); }
        }

        async function loadTrend() {
          if (!trendRegion.value) return;
          trendError.value = '';
          try {
            const [res, blockRes] = await Promise.all([
              api.get('/analysis/trend/' + encodeURIComponent(trendRegion.value) + '?months=12'),
              api.get('/analysis/blocks?region=' + encodeURIComponent(trendRegion.value)),
            ]);
            const data = await res.json();
            const blockData = await blockRes.json().catch(() => null);
            trendBlocks.value = (blockData && blockData.status === 'ok') ? blockData : null;
            // 板块字段覆盖不足（少于 3 个）时自动切到「热门小区」口径
            const bLen = (trendBlocks.value && trendBlocks.value.blocks || []).length;
            const cLen = (trendBlocks.value && trendBlocks.value.communities || []).length;
            trendBlockMode.value = (bLen < 3 && cLen > 0) ? 'community' : 'block';
            if (data.status === 'error') { trendError.value = data.error || '该区域暂无数据'; trendData.value = null; return; }
            trendData.value = data;
            await nextTick();
            renderTrendChart();
          } catch(e) { console.error(e); trendError.value = '走势数据加载失败'; }
        }

        /* ---------------- 登录滑块拼图验证 ----------------
         * 背景图来自本地素材库（/static/images/captcha/，房源实景图），
         * 每次随机挑一张、随机裁一段画面、随机定拼图位置与缺口位置。
         * 常态隐藏，点「登录」时才以弹窗形式出现。
         * 纯前端校验，用于挡住脚本硬点登录；若要做强校验需在服务端签发挑战。
         */
        const showCaptchaModal = ref(false);
        const captchaPurpose = ref('login');   // login | register：验证通过后继续哪个动作
        const captchaPassed = ref(false);
        const captchaLoading = ref(false);
        const captchaState = ref('idle');       // idle | dragging | success | fail
        const captchaOffset = ref(0);           // 滑块位移（px）
        const captchaPieceX = ref(8);           // 拼图块当前 left（px）
        const captchaPieceY = ref(20);          // 拼图块 top（px）
        const captchaStageRef = ref(null);
        const captchaBgRef = ref(null);
        const captchaPieceRef = ref(null);
        const captchaBarRef = ref(null);
        const captchaHandleRef = ref(null);
        const CAPTCHA_HANDLE_W = 42;
        // 本地图库：来源 Unsplash（可自由使用），已裁成 1000x360 存放在项目里
        const CAPTCHA_IMAGES = [
          '/static/images/captcha/captcha1.jpg',
          '/static/images/captcha/captcha2.jpg',
          '/static/images/captcha/captcha3.jpg',
          '/static/images/captcha/captcha4.jpg',
          '/static/images/captcha/captcha5.jpg',
          '/static/images/captcha/captcha6.jpg',
        ];
        const captchaImgCache = {};
        let captchaGeo = null;
        let captchaDragFrom = 0;
        let lastCaptchaIndex = -1;

        /** 随机画面：随机色相天空 + 随机楼群 + 随机窗格 */
        function drawCaptchaScene(ctx, W, H) {
          const hue = Math.floor(Math.random() * 360);
          const sky = ctx.createLinearGradient(0, 0, W * 0.4, H);
          sky.addColorStop(0, 'hsl(' + hue + ', 78%, 72%)');
          sky.addColorStop(0.55, 'hsl(' + ((hue + 28) % 360) + ', 70%, 56%)');
          sky.addColorStop(1, 'hsl(' + ((hue + 58) % 360) + ', 64%, 38%)');
          ctx.fillStyle = sky;
          ctx.fillRect(0, 0, W, H);

          // 两团柔光，避免大面积纯色
          for (let i = 0; i < 2; i += 1) {
            const cx = Math.random() * W;
            const cy = Math.random() * H * 0.6;
            const r = 40 + Math.random() * 70;
            const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
            glow.addColorStop(0, 'rgba(255,255,255,0.35)');
            glow.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.fillStyle = glow;
            ctx.fillRect(0, 0, W, H);
          }

          // 随机楼群：宽度、高度、颜色都随机
          let x = -20 - Math.random() * 30;
          while (x < W + 20) {
            const w = 24 + Math.random() * 46;
            const h = 28 + Math.random() * (H * 0.62);
            const y = H - h - Math.random() * 10;
            const l = 16 + Math.random() * 22;
            ctx.fillStyle = 'hsla(' + ((hue + 170 + Math.random() * 50) % 360)
              + ', ' + (28 + Math.random() * 26) + '%, ' + l + '%, ' + (0.6 + Math.random() * 0.3) + ')';
            ctx.fillRect(x, y, w, h);

            ctx.fillStyle = 'rgba(255, 246, 205, ' + (0.35 + Math.random() * 0.45) + ')';
            for (let wy = y + 8; wy < H - 10; wy += 12) {
              for (let wx = x + 6; wx < x + w - 8; wx += 11) {
                if (Math.random() > 0.45) ctx.fillRect(wx, wy, 5, 6);
              }
            }
            x += w + 3 + Math.random() * 9;
          }

          // 地面
          ctx.fillStyle = 'rgba(4, 16, 10, 0.28)';
          ctx.fillRect(0, H - 12, W, 12);
        }

        /** 拼图块轮廓：圆角方块 + 右侧半圆凸起（宽 size + knob） */
        function captchaPiecePath(ctx, x, y, size, knob) {
          const r = 5;
          const cx = x + size;
          const cy = y + size / 2;
          ctx.beginPath();
          ctx.moveTo(x + r, y);
          ctx.lineTo(cx, y);
          ctx.lineTo(cx, cy - knob);
          ctx.arc(cx, cy, knob, -Math.PI / 2, Math.PI / 2, false);
          ctx.lineTo(cx, y + size);
          ctx.lineTo(x + r, y + size);
          ctx.quadraticCurveTo(x, y + size, x, y + size - r);
          ctx.lineTo(x, y + r);
          ctx.quadraticCurveTo(x, y, x + r, y);
          ctx.closePath();
        }

        function loadCaptchaImage(src) {
          if (captchaImgCache[src]) return Promise.resolve(captchaImgCache[src]);
          return new Promise((resolve) => {
            const img = new Image();
            img.onload = () => { captchaImgCache[src] = img; resolve(img); };
            img.onerror = () => resolve(null);
            img.src = src;
          });
        }

        /** 按 cover 铺满画布，随机放大 + 随机裁切 + 随机镜像，同一张图每次也不一样 */
        function drawCaptchaPhoto(ctx, img, W, H) {
          const zoom = 1 + Math.random() * 0.25;
          const scale = Math.max(W / img.width, H / img.height) * zoom;
          const sw = W / scale;
          const sh = H / scale;
          const sx = Math.random() * Math.max(0, img.width - sw);
          const sy = Math.random() * Math.max(0, img.height - sh);

          ctx.save();
          if (Math.random() < 0.5) {
            ctx.translate(W, 0);
            ctx.scale(-1, 1);
          }
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, W, H);
          ctx.restore();
        }

        function openCaptchaModal(purpose) {
          captchaPurpose.value = purpose === 'register' ? 'register' : 'login';
          showCaptchaModal.value = true;
          nextTick(() => initCaptcha());
        }

        function closeCaptchaModal() {
          showCaptchaModal.value = false;
          if (captchaState.value !== 'success') captchaState.value = 'idle';
        }

        async function initCaptcha() {
          captchaPassed.value = false;
          captchaState.value = 'idle';
          captchaOffset.value = 0;
          captchaLoading.value = true;

          // 随机挑图，且避免连续两次抽到同一张
          let pick = Math.floor(Math.random() * CAPTCHA_IMAGES.length);
          if (CAPTCHA_IMAGES.length > 1 && pick === lastCaptchaIndex) {
            pick = (pick + 1 + Math.floor(Math.random() * (CAPTCHA_IMAGES.length - 1)))
              % CAPTCHA_IMAGES.length;
          }
          lastCaptchaIndex = pick;
          const img = await loadCaptchaImage(CAPTCHA_IMAGES[pick]);
          await nextTick();

          const stage = captchaStageRef.value;
          const bg = captchaBgRef.value;
          const piece = captchaPieceRef.value;
          const bar = captchaBarRef.value;
          if (!stage || !bg || !piece || !bar) { captchaLoading.value = false; return; }

          const W = Math.round(stage.clientWidth) || 420;
          const H = Math.round(stage.clientHeight) || 150;
          const dpr = Math.min(window.devicePixelRatio || 1, 2);
          const size = 46;
          const knob = 10;
          const boxW = size + knob;
          const startX = 8;
          const y = 14 + Math.random() * Math.max(1, H - size - 28);
          const travel = Math.max(30, W - boxW - startX - 12);
          // 缺口至少和起始拼图块隔开一段距离，避免刚拖两下就撞上
          const minX = Math.min(startX + boxW + 12, startX + travel);
          const targetX = minX + Math.random() * Math.max(1, startX + travel - minX);

          bg.width = Math.round(W * dpr);
          bg.height = Math.round(H * dpr);
          bg.style.width = W + 'px';
          bg.style.height = H + 'px';
          const bctx = bg.getContext('2d');
          bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          // 图片没加载出来（断网/文件丢失）时退回本地绘制的街景
          if (img) drawCaptchaPhoto(bctx, img, W, H);
          else drawCaptchaScene(bctx, W, H);

          // 目标缺口
          bctx.save();
          captchaPiecePath(bctx, targetX, y, size, knob);
          bctx.fillStyle = 'rgba(6, 20, 14, 0.55)';
          bctx.fill();
          bctx.lineWidth = 1.5;
          bctx.strokeStyle = 'rgba(255,255,255,0.85)';
          bctx.stroke();
          bctx.restore();

          // 拼图块：从同一画面里裁出来，保证纹理完全对得上
          piece.width = Math.round(boxW * dpr);
          piece.height = Math.round(size * dpr);
          piece.style.width = boxW + 'px';
          piece.style.height = size + 'px';
          const pctx = piece.getContext('2d');
          pctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          pctx.clearRect(0, 0, boxW, size);
          pctx.save();
          captchaPiecePath(pctx, 0, 0, size, knob);
          pctx.clip();
          pctx.drawImage(bg, targetX * dpr, y * dpr, boxW * dpr, size * dpr, 0, 0, boxW, size);
          pctx.restore();
          captchaPiecePath(pctx, 0, 0, size, knob);
          pctx.strokeStyle = 'rgba(255,255,255,0.9)';
          pctx.lineWidth = 1.5;
          pctx.stroke();

          captchaGeo = {
            size: size, knob: knob, boxW: boxW, startX: startX,
            travel: travel, targetX: targetX,
            maxOffset: Math.max(1, bar.clientWidth - CAPTCHA_HANDLE_W),
          };
          captchaPieceX.value = startX;
          captchaPieceY.value = y;
          captchaLoading.value = false;
        }

        function onCaptchaDown(e) {
          if (!captchaGeo || captchaState.value === 'success') return;
          if (e && e.preventDefault) e.preventDefault();
          captchaState.value = 'dragging';
          captchaDragFrom = e.clientX - captchaOffset.value;
          window.addEventListener('pointermove', onCaptchaMove);
          window.addEventListener('pointerup', onCaptchaUp);
          window.addEventListener('pointercancel', onCaptchaUp);
        }

        function onCaptchaMove(e) {
          if (captchaState.value !== 'dragging' || !captchaGeo) return;
          const next = Math.min(Math.max(0, e.clientX - captchaDragFrom), captchaGeo.maxOffset);
          captchaOffset.value = next;
          captchaPieceX.value = captchaGeo.startX + (next / captchaGeo.maxOffset) * captchaGeo.travel;
        }

        function onCaptchaUp() {
          window.removeEventListener('pointermove', onCaptchaMove);
          window.removeEventListener('pointerup', onCaptchaUp);
          window.removeEventListener('pointercancel', onCaptchaUp);
          if (captchaState.value !== 'dragging' || !captchaGeo) return;

          if (Math.abs(captchaPieceX.value - captchaGeo.targetX) <= 5) {
            captchaPieceX.value = captchaGeo.targetX;
            captchaState.value = 'success';
            captchaPassed.value = true;
            loginError.value = '';
            // 让用户看清「验证通过」，随后自动收起验证弹窗，继续登录或注册
            setTimeout(() => {
              if (captchaPassed.value && showCaptchaModal.value) {
                const purpose = captchaPurpose.value;
                closeCaptchaModal();
                if (purpose === 'register') doRegister();
                else doLogin();
              }
            }, 450);
          } else {
            captchaState.value = 'fail';
            setTimeout(() => {
              if (captchaState.value === 'fail') initCaptcha();
            }, 700);
          }
        }

        const captchaBarText = computed(() => {
          if (captchaState.value === 'success') return '验证通过';
          if (captchaState.value === 'fail') return '未对齐，正在换一张…';
          if (captchaState.value === 'dragging') return '拖到缺口处松手';
          return '按住滑块，拖动完成拼图';
        });

        // 任何入口打开登录框（收藏/评论拦截、注册页跳转等）都重置验证状态，等待点「登录」再出题
        watch(showLoginModal, (open) => {
          if (open) {
            loginError.value = '';
            captchaPassed.value = false;
            closeCaptchaModal();
          }
        });

        // 注册框同理：每次打开都要求重新验证，登录/注册切换不会复用手势
        watch(showRegisterModal, (open) => {
          if (open) {
            registerError.value = '';
            captchaPassed.value = false;
            closeCaptchaModal();
          }
        });

        // ===== 站内消息（评论被回复提醒） =====
        const notifyOpen = ref(false);
        const notifyList = ref([]);
        const notifyUnread = ref(0);
        const notifyTotal = ref(0);
        const notifyPage = ref(1);
        const notifyLoading = ref(false);
        const NOTIFY_PAGE_SIZE = 10;

        /** 未读数：登录后轮询，用于导航栏角标 */
        async function loadNotifyUnread() {
          if (!currentUser.value) { notifyUnread.value = 0; return; }
          try {
            const res = await api.get('/notifications/unread-count');
            if (!res.ok) return;
            const data = await res.json();
            notifyUnread.value = data.unread || 0;
          } catch(e) { /* 消息是附属功能，失败静默 */ }
        }

        async function loadNotifications(reset) {
          if (!currentUser.value) { notifyList.value = []; notifyTotal.value = 0; return; }
          if (reset) { notifyPage.value = 1; notifyList.value = []; }
          notifyLoading.value = true;
          try {
            const res = await api.get('/notifications?page=' + notifyPage.value +
              '&page_size=' + NOTIFY_PAGE_SIZE);
            if (res.ok) {
              const data = await res.json();
              const known = new Set(notifyList.value.map(n => n.id));
              notifyList.value = notifyList.value.concat(
                (data.items || []).filter(n => !known.has(n.id)));
              notifyTotal.value = data.total || 0;
              notifyUnread.value = data.unread || 0;
            }
          } catch(e) { console.error(e); }
          notifyLoading.value = false;
        }

        function toggleNotifyPanel() {
          if (!currentUser.value) { showLoginModal.value = true; return; }
          notifyOpen.value = !notifyOpen.value;
          showUserMenu.value = false;
          if (notifyOpen.value) loadNotifications(true);
        }

        function loadMoreNotify() {
          if (notifyLoading.value) return;
          notifyPage.value += 1;
          loadNotifications(false);
        }

        async function markNotifyRead(n) {
          if (!n || n.is_read) return;
          n.is_read = true;                 // 先本地置为已读，交互不等待请求
          notifyUnread.value = Math.max(0, notifyUnread.value - 1);
          try { await api.post('/notifications/' + n.id + '/read', {}); } catch(e) {}
        }

        async function markAllNotifyRead() {
          if (!notifyUnread.value) return;
          notifyList.value.forEach(n => { n.is_read = true; });
          notifyUnread.value = 0;
          try { await api.post('/notifications/read-all', {}); } catch(e) {}
        }

        /** 点消息：标记已读 → 打开对应房源 → 滚到评价区 */
        async function openNotification(n) {
          markNotifyRead(n);
          notifyOpen.value = false;
          if (!n || !n.house_id) return;
          try {
            const res = await api.get('/houses/' + n.house_id);
            if (!res.ok) return;
            const house = await res.json();
            // 兜底：接口异常时不要跳进一个空白详情页
            if (!house || !house.id) return;
            viewHouse(house);
            setTimeout(() => {
              const el = document.querySelector('.comment-list') ||
                document.querySelector('.dp-sec');
              if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 900);
          } catch(e) { console.error(e); }
        }

        /** 消息时间：刚刚 / N 分钟前 / N 小时前 / 昨天 / N 天前 / 月日 */
        function formatNotifyTime(v) {
          if (!v) return '';
          const t = new Date(String(v).replace(' ', 'T'));
          if (isNaN(t.getTime())) return String(v).replace('T', ' ').slice(0, 16);
          const diff = (Date.now() - t.getTime()) / 1000;
          if (diff < 60) return '刚刚';
          if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
          if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
          if (diff < 172800) return '昨天';
          if (diff < 2592000) return Math.floor(diff / 86400) + ' 天前';
          return (t.getMonth() + 1) + '月' + t.getDate() + '日';
        }

        // ===== AI 看房报告（图片理解 + 周边 POI + 走势 + 估价，由后端聚合生成） =====
        const reportOpen = ref(false);
        const reportLoading = ref(false);
        const reportError = ref('');
        const reportText = ref('');
        const reportSub = ref('');
        const reportHouseTitle = ref('');
        // 缓存归属：只有仍是同一套房源时才复用，否则换房会显示上一套的报告
        const reportHouseId = ref(0);
        let reportReqSeq = 0;

        async function openViewingReport(force) {
          const house = detailHouse.value;
          if (!house || !house.id) return;
          reportOpen.value = true;
          reportHouseTitle.value = [house.region, house.address || house.title]
            .filter(Boolean).join(' · ');
          // 同一套房源已生成过且不是强制刷新，直接复用（后端也有 10 分钟缓存）
          if (force !== true && reportText.value && reportHouseId.value === house.id) return;

          reportLoading.value = true;
          reportError.value = '';
          reportText.value = '';
          reportSub.value = '';
          reportHouseId.value = 0;
          // 连点不同房源时只认最后一次请求，避免慢请求回来覆盖新报告
          const seq = ++reportReqSeq;
          try {
            const url = '/analysis/viewing-report/' + house.id + (force === true ? '?refresh=true' : '');
            const res = await api.get(url);
            const data = await res.json();
            if (seq !== reportReqSeq) return;
            if (!res.ok || data.status !== 'ok') {
              reportError.value = data.detail || data.error || '报告生成失败，请稍后重试';
            } else {
              reportText.value = data.report || '';
              reportSub.value = (data.degraded ? '模板生成' : (data.model || 'AI')) +
                (data.latency ? ' · ' + data.latency + 's' : '');
              // 以后端返回的 house_id 为准，避免前端状态漂移
              reportHouseId.value = data.house_id || house.id;
            }
          } catch(e) {
            if (seq !== reportReqSeq) return;
            reportError.value = '网络异常，报告生成失败';
          }
          if (seq === reportReqSeq) reportLoading.value = false;
        }

        /** 报告是模型输出的 Markdown，这里做最小渲染：标题 / 列表 / 加粗，其余按段落，先转义再拼标签 */
        const reportHtml = computed(() => {
          const raw = reportText.value || '';
          if (!raw) return '';
          const out = [];
          raw.split('\n').forEach(line => {
            let s = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            s = s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
            if (/^#{2,4}\s+/.test(s)) {
              out.push('<div class="rp-h2">' + s.replace(/^#{2,4}\s+/, '') + '</div>');
            } else if (/^[-*]\s+/.test(s)) {
              out.push('<div class="rp-li">' + s.replace(/^[-*]\s+/, '') + '</div>');
            } else if (!s.trim()) {
              out.push('<div class="rp-gap"></div>');
            } else {
              out.push('<div class="rp-p">' + s + '</div>');
            }
          });
          return out.join('');
        });

        async function copyReportText() {
          if (!reportText.value) return;
          try {
            await navigator.clipboard.writeText(reportText.value);
          } catch(e) {
            console.warn('复制失败', e);
          }
        }

        function openLoginModal() {
          showUserMenu.value = false;
          showLoginModal.value = true;
          loginError.value = '';
        }
        function openRegisterModal() {
          showUserMenu.value = false;
          showRegisterModal.value = true;
          registerError.value = '';
        }
        async function doLogin() {
          loginError.value = '';
          if (!loginForm.value.username || !loginForm.value.password) {
            loginError.value = '请输入用户名和密码';
            return;
          }
          if (!captchaPassed.value) {
            // 账号密码先校验，再弹验证码，避免一上来就打断
            openCaptchaModal();
            return;
          }
          loginLoading.value = true;
          try {
            const res = await api.post('/user/login', {
                username: loginForm.value.username,
                password: loginForm.value.password,
              });
            const data = await res.json();
            const userObj = extractUser(data);
            if (res.ok && userObj) {
              // 登录时即确定身份与权限：Token 统一保存，permissions 决定可见入口
              authToken.value = data.token || '';
              api.setAuthToken(authToken.value);
              setSessionUser(userObj);
              // 记住密码：勾选时把「用户名 + 密码（Base64）」保存在本机，下次自动填充
              try {
                const uname = data.user.username || data.user.name || '';
                const rawPwd = loginForm.value.password || '';
                // 管理员账号一律不落盘：勾了「记住密码」也不保存，避免共用电脑留凭据
                if (rememberPassword.value && uname && rawPwd && userObj.role !== 'admin') {
                  localStorage.setItem('remember_login', JSON.stringify({ u: uname, p: encodeB64(rawPwd) }));
                } else {
                  localStorage.removeItem('remember_login');
                }
                localStorage.removeItem('remember_username');
              } catch(e) {}
              localStorage.setItem('house_token', data.token || '');
              showLoginModal.value = false;
              // 本次验证一次有效：退出/下次打开登录框会重新出题
              captchaPassed.value = false;
              closeCaptchaModal();
              // 重新加载收藏、对比、推荐等用户数据
              loadFavorites();
              loadCompare();
              loadHistory();
              loadRecommend();
              // 登录后立刻拉一次未读消息，有回复时导航栏直接出红点
              loadNotifyUnread();
            } else {
              loginError.value = data.detail || '登录失败，请检查用户名和密码';
            }
          } catch(e) {
            loginError.value = '网络错误，请稍后重试';
          }
          loginLoading.value = false;
        }
        async function doRegister() {
          registerError.value = '';
          if (!registerForm.value.username || registerForm.value.username.length < 3) {
            registerError.value = '用户名至少3个字符';
            return;
          }
          if (!registerForm.value.password || registerForm.value.password.length < 6) {
            registerError.value = '密码至少6位';
            return;
          }
          if (registerForm.value.password !== registerForm.value.confirmPassword) {
            registerError.value = '两次密码输入不一致';
            return;
          }
          if (!captchaPassed.value) {
            // 表单先校验，再弹验证码，避免填错就被打断
            openCaptchaModal('register');
            return;
          }
          registerLoading.value = true;
          try {
            const res = await api.post('/user/register', {
                username: registerForm.value.username,
                password: registerForm.value.password,
                email: registerForm.value.email,
              });
            const data = await res.json();
            const userObj = extractUser(data);
            if (res.ok && userObj) {
              // 注册成功自动登录（后端同时下发 Token，保证改密等接口可用）
              authToken.value = data.token || '';
              api.setAuthToken(authToken.value);
              setSessionUser(userObj);
              if (data.token) localStorage.setItem('house_token', data.token);
              showRegisterModal.value = false;
              captchaPassed.value = false;
              closeCaptchaModal();
              loadFavorites();
              loadCompare();
              loadHistory();
              loadRecommend();
              alert('注册成功！欢迎加入安居乐');
            } else {
              registerError.value = data.detail || '注册失败，请稍后重试';
            }
          } catch(e) {
            registerError.value = '网络错误，请稍后重试';
          }
          registerLoading.value = false;
        }
        function doLogout() {
          const wasAdmin = !!(currentUser.value && currentUser.value.role === 'admin');
          currentUser.value = null;
          permissions.value = [];
          authToken.value = '';
          api.setAuthToken('');
          // 前台与后台共用一套身份：退出时两边的存储一起清，
          // 否则退出后再打开 /admin 会通过 SSO 自动进后台（管理员账号尤其明显）
          ['house_token', 'house_user', 'admin_token', 'admin_info', 'admin_remember', 'adminToken']
            .forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });
          ['house_token', 'house_user', 'admin_token', 'admin_info', 'admin_sso']
            .forEach(k => { try { sessionStorage.removeItem(k); } catch (e) {} });
          // 退出后登录框不再回填上一次的账号：统一清空输入框；
          // 普通用户若勾过「记住密码」，刷新页面后会从 remember_login 恢复；
          // 管理员则连记住的账号密码一起删掉，界面与存储都不留痕迹。
          loginForm.value = { username: '', password: '' };
          if (wasAdmin) {
            try {
              localStorage.removeItem('remember_login');
              localStorage.removeItem('remember_username');
              rememberPassword.value = false;
            } catch (e) {}
          }
          showUserMenu.value = false;
          favHouses.value = [];
          compareHouses.value = [];
          historyList.value = [];
          // 消息按用户隔离，退出时清空，避免下一个账号看到上一个人的消息
          chatCid.value = '';
          try { sessionStorage.removeItem(CHAT_CID_KEY); } catch (e) {}
          messages.value = [{ role: 'ai', content: CHAT_WELCOME }];
          notifyOpen.value = false;
          notifyList.value = [];
          notifyUnread.value = 0;
          notifyTotal.value = 0;
          // 退出 = 整体下线：本页打开的后台标签直接关掉；
          // 不是本页打开的（书签/地址栏）拿不到句柄，用 storage 信号让后台标签自己关
          try {
            if (adminWindow && !adminWindow.closed) adminWindow.close();
          } catch (e) {}
          adminWindow = null;
          try { localStorage.setItem('admin_force_logout', String(Date.now())); } catch (e) {}
        }
        function checkLoginStatus() {
          const savedUser = localStorage.getItem('house_user');
          const savedToken = localStorage.getItem('house_token');
          if (!savedUser || !savedToken) {
            // 只剩一半的登录态（有 user 无 token / 有 token 无 user）直接作废，
            // 避免退出不彻底时出现「界面显示已登录、接口全 401」
            if (savedUser || savedToken) {
              try {
                localStorage.removeItem('house_user');
                localStorage.removeItem('house_token');
              } catch(e) {}
              authToken.value = '';
              api.setAuthToken('');
            }
            return;
          }
          try {
            // setSessionUser 会把 permissions 归一化后写回，兼容旧登录态（无 permissions 字段）
            setSessionUser(JSON.parse(savedUser));
          } catch(e) {}
          if (authToken.value) api.setAuthToken(authToken.value);
        }

        onMounted(() => {
          // 恢复本标签页的会话 ID：刷新后还能接着上一轮聊（后端记忆按 thread_id 存）
          try { chatCid.value = sessionStorage.getItem(CHAT_CID_KEY) || ''; } catch (e) {}
          checkLoginStatus();
          loadRegions();
          loadHotHouses();
          loadRecommend();
          loadStats();
          loadKnowledgeStats();
          // 恢复登录态后拉一次未读消息，并每分钟轮询角标（轻量接口）
          setTimeout(() => { if (currentUser.value) loadNotifyUnread(); }, 1200);
          setInterval(() => { if (currentUser.value && !notifyOpen.value) loadNotifyUnread(); }, 60000);
          window.addEventListener('resize', () => {
            Object.values(chartInstances).forEach(c => { try { c.resize(); } catch(e){} });
          });
          // 供地图 InfoWindow 调用
          window._viewHouseById = function(id) {
            const house = mapHouses.value.find(function(h) { return h.id === id; });
            if (house) viewHouse(house);
          };
          // 深链接：?house=123 直接打开详情页（刷新/分享后仍能回到同一套房）
          const initialId = new URLSearchParams(window.location.search).get('house');
          if (initialId) {
            api.get('/houses/' + initialId)
              .then(function (r) { return r.ok ? r.json() : null; })
              .then(function (h) { if (h && h.id) viewHouse(h); })
              .catch(function () {});
          }
        });

        return {
          tab, searchKw, headerKw, doSearchFromHeader,
          houses, hotHouses, recHouses, homeRegions, mapHouses, mapRegion,
          mapLoaded, detailHouse, favHouses, compareHouses, historyList,
          detailMetrics, similarHouses, closeHouseDetail, contactOwner,
          compareVerdict, compareSections, compareLoading,
          askPrice, askResult, estError, estFactorsTotal, checkAskPrice,
          estRegionStat, estRegionRank, estHistory, applyEstSample, restoreEstimate, clearEstHistory,
          filter, messages, chatInput, chatLoading, estForm, estData, estLoading,
          nearbyTabs, nearbyItems, nearbyTab, nearbyLoading, nearbyMapReady,
          setNearbyTab, focusNearby, formatDistance,
          mlData, mlError, userProfile, recStrategy, homeRecHouses,
          dashData, dashLoading,
          trendRegions, trendRegion, trendData, trendOverview, trendBlocks, trendError,
          trendRange, trendRangeText, setTrendRange,
          trendBlockMode, trendBlockList, trendBlockMaxAbs,
          chatBodyRef, navItems, activeDropdown, regions, findView, priceView, chatView,
          setFindView, setPriceView, setChatView, openFind, openPrice, suggestions, knowledgeDocs,
          knowledgeCategories, knowledgeCat, showKnowledgeDetail, currentKnowledge,
          filteredKnowledgeDocs, viewKnowledge,
          kbDocs, kbTotal, kbPage, kbPageSize, kbLoading, kbUploading, kbRebuilding,
          kbSelectedFile, kbFileInput, kbMessage, kbMessageType, kbStats, canKnowledge,
          loadKnowledge, loadKnowledgeDocs, loadKnowledgeStats, onKnowledgeFile,
          uploadKnowledge, removeKnowledgeDoc, rebuildHouseIndex, formatFileSize,
          authToken, isAdmin, canManage, can, permissions, visibleNavItems, displayName, avatarText,
          openAdmin,
          showPwdModal, pwdForm, pwdLoading, pwdError, pwdSuccess,
          pwdVisible, pwdStrength, openPwdModal, openPwdFromLogin, doChangePassword,
          currentUser, showUserMenu, mobileMenuOpen, showLoginModal, showRegisterModal,
          loginForm, loginLoading, loginError, registerForm, registerLoading, registerError,
          rememberPassword, onRememberPasswordChange,
          showCaptchaModal, captchaPurpose, captchaPassed, captchaLoading, captchaState, captchaOffset,
          captchaPieceX, captchaPieceY,
          captchaStageRef, captchaBgRef, captchaPieceRef, captchaBarRef, captchaHandleRef,
          captchaBarText, initCaptcha, onCaptchaDown, openCaptchaModal, closeCaptchaModal,
          stats, mapHousesTotal, mapPage, mapPageSize, mapHousesAll,
          housePage, housePageSize, totalHouses, changeHousePage,
          pageJump, houseTotalPages, jumpHousePage,
          formatPrice, switchTab, doSearch, loadHouses, loadHotHouses, loadStats,
          loadRecommend, loadMapHouses, initMap, focusOnMap, viewHouse,
          changeMapPage, applyMapPage,
          loadFavorites, isFavorited, toggleFavorite, loadCompare,
          isCompared, toggleCompare, loadHistory, sendChat, newChat, chatCid, doEstimate,
          loadDashboard,
          loadTrendRegions, loadTrend,
          openLoginModal, openRegisterModal, doLogin, doRegister, doLogout,
          removeFromHistory, clearHistory,
          comments, commentTotal, commentAvg, commentText, commentScore,
          commentLoading, commentLoadingMore, commentError,
          houseSenti, houseSentiLoading, loadHouseSentiment,
          loadComments, loadMoreComments, isMyComment, commentAvatar, commentAvatarStyle,
          submitComment, deleteMyComment, formatCommentTime,
          replyTargetId, replyToName, replyText, replyLoading, replyError,
          toggleReply, cancelReply, submitReply,
          notifyOpen, notifyList, notifyUnread, notifyTotal, notifyLoading,
          toggleNotifyPanel, loadMoreNotify, markAllNotifyRead, openNotification, formatNotifyTime,
          reportOpen, reportLoading, reportError, reportText, reportHtml, reportSub, reportHouseTitle,
          openViewingReport, copyReportText,
        };
      }
    }).mount('#app');
  
