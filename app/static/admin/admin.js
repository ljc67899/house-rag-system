/* =========================================
 * 管理后台逻辑（独立于前台 main.js）
 * 只依赖 api.js 提供的请求层，不复用前台任何 Vue 状态
 * ========================================= */
const { createApp, ref, computed, onMounted, nextTick } = Vue;

const TOKEN_KEY = 'admin_token';
const ADMIN_KEY = 'admin_info';
const REMEMBER_KEY = 'admin_remember';

createApp({
  setup() {
    /* ---------------- 登录态 ----------------
     * 默认只在当前标签页保留登录态（sessionStorage），关掉标签页就要重新登录；
     * 勾选「记住登录」才写入 localStorage。旧版本不分青红皂白写在 localStorage 里，
     * 这里做一次清理，强制回登录页，避免"打开 /admin 就直接进后台"。
     */
    if (localStorage.getItem(TOKEN_KEY) && localStorage.getItem(REMEMBER_KEY) !== '1') {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(ADMIN_KEY);
    }
    const storedToken = localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY) || '';
    const token = ref(storedToken);
    const admin = ref(JSON.parse(
      localStorage.getItem(ADMIN_KEY) || sessionStorage.getItem(ADMIN_KEY) || '{}'
    ));
    const remember = ref(localStorage.getItem(REMEMBER_KEY) === '1');
    const authChecking = ref(true);   // 先校验登录态（含单点登录），避免闪一下登录页或后台
    const loginForm = ref({ username: '', password: '' });
    const loginError = ref('');
    const logging = ref(false);

    /* ---------------- 人机验证（滑块拼图） ----------------
     * 密码登录、一键进入都要先过拼图，挡住脚本直接刷接口/自动进后台。
     */
    let captchaWidget = null;
    async function humanVerify() {
      if (!window.PuzzleCaptcha) return true;   // 组件没加载时不影响正常登录
      if (!captchaWidget) captchaWidget = new window.PuzzleCaptcha();
      return await captchaWidget.verify();
    }

    if (token.value) api.setAuthToken(token.value);

    async function doLogin() {
      loginError.value = '';
      if (!loginForm.value.username || !loginForm.value.password) {
        loginError.value = '请输入账号和密码';
        return;
      }
      // 先过人机验证，再提交账号密码
      if (!(await humanVerify())) return;
      logging.value = true;
      try {
        const res = await api.post('/user/login', {
          username: loginForm.value.username.trim(),
          password: loginForm.value.password,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          loginError.value = data.detail || '账号或密码错误';
          return;
        }
        if (data.role !== 'admin') {
          loginError.value = '该账号不是管理员，请使用 admin 账号登录';
          return;
        }
        token.value = data.token;
        admin.value = data.admin || data.user || { username: loginForm.value.username };
        api.setAuthToken(data.token);
        const store = remember.value ? localStorage : sessionStorage;
        store.setItem(TOKEN_KEY, data.token);
        store.setItem(ADMIN_KEY, JSON.stringify(admin.value));
        if (remember.value) {
          localStorage.setItem(REMEMBER_KEY, '1');
          sessionStorage.removeItem(TOKEN_KEY);
          sessionStorage.removeItem(ADMIN_KEY);
        } else {
          localStorage.removeItem(REMEMBER_KEY);
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(ADMIN_KEY);
        }
        authChecking.value = false;
        switchView('overview');
        // 区域下拉与图库依赖登录态，登录成功后预取一次（onMounted 在未登录时会提前 return）
        loadRegions();
        loadGallery();
      } catch (e) {
        loginError.value = '网络异常，请检查服务是否启动';
      } finally {
        logging.value = false;
      }
    }

    function doLogout() {
      // 一套身份一处登录，也意味着一处退出：前台登录态一并清掉，
      // 否则退出后台后重新打开又会通过单点登录自动进来。
      token.value = '';
      admin.value = {};
      api.setAuthToken('');
      ['admin_token', 'admin_info', 'admin_remember', 'house_token', 'house_user', 'remember_login']
        .forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });
      ['admin_token', 'admin_info', 'admin_sso', 'house_token', 'house_user']
        .forEach(k => { try { sessionStorage.removeItem(k); } catch (e) {} });
      authChecking.value = false;
      remember.value = false;   // 退出后不再默认勾选「记住登录」，避免下次无意持久化
      loginForm.value = { username: '', password: '' };
    }

    /**
     * 前台退出登录时会把 admin_force_logout 写进 localStorage：
     * 其它标签页收到 storage 事件 → 清掉登录态并关闭自己，做到「前台退出 = 后台一起下线」。
     * 由前台 window.open 打开的标签可以关；手动从地址栏打开的标签浏览器不允许脚本关闭，
     * 兜底是停在登录页（doLogout 已清空 token）。
     */
    window.addEventListener('storage', (e) => {
      if (e.key !== 'admin_force_logout' || !e.newValue) return;
      doLogout();
      try { window.close(); } catch (err) {}
    });

    /* ---------------- 单点登录：直接复用前台管理员身份 ----------------
     * 行业常见做法是「一套身份、一处登录」：前台登录拿到 token 后，
     * 进后台只做角色校验（/user/admin/me）就直接放行，不再要求二次登录、
     * 也不再多加一道验证码；验证码只在真正输入账号密码的登录表单上出现。
     */
    async function tryFrontendSso() {
      const frontToken = (localStorage.getItem('house_token')
        || sessionStorage.getItem('house_token') || '').trim();
      if (!frontToken) return false;
      try {
        const res = await fetch('/api/user/admin/me', {
          headers: { Authorization: 'Bearer ' + frontToken },
        });
        if (!res.ok) return false;
        const data = await res.json().catch(() => ({}));
        if (!data || !data.valid) return false;
        token.value = frontToken;
        admin.value = data.admin || { username: 'admin' };
        api.setAuthToken(frontToken);
        // 后台标签页内沿用这次会话，刷新不用重新校验
        sessionStorage.setItem(TOKEN_KEY, frontToken);
        sessionStorage.setItem(ADMIN_KEY, JSON.stringify(admin.value));
        sessionStorage.setItem('admin_sso', '1');
        return true;
      } catch (e) {
        return false;
      }
    }

    /* ---------------- 通用 ---------------- */
    const toast = ref({ text: '', error: false });
    let toastTimer = null;
    function notify(text, error = false) {
      toast.value = { text, error };
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { toast.value = { text: '', error: false }; }, 2600);
    }

    function formatTime(value) {
      if (!value) return '—';
      const text = String(value).replace('T', ' ');
      return text.slice(0, 16);
    }

    const pageSize = 20;

    /* ---------------- 视图切换 ---------------- */
    const view = ref('overview');
    const viewMeta = computed(() => ({
      overview: { title: '概览', sub: '房源 / 用户 / 评论的整体运行情况' },
      houses: { title: '房源管理', sub: '增删改查、批量删除、CSV 导入导出' },
      users: { title: '用户管理', sub: '查看注册用户、重置密码、删除账号' },
      comments: { title: '评论管理', sub: '检索与清理房源评论' },
      knowledge: { title: '知识库', sub: '上传知识文档、重建房源向量索引' },
    }[view.value]));

    function switchView(next) {
      view.value = next;
      if (next === 'overview') loadOverview();
      if (next === 'houses') loadHouses();
      if (next === 'users') loadUsers(1);
      if (next === 'comments') loadComments(1);
      if (next === 'knowledge') { loadDocs(1); }
    }

    function refreshCurrent() {
      switchView(view.value);
      notify('已刷新');
    }

    /* ---------------- 概览 ---------------- */
    const overview = ref({
      houses: { total: 0, today_new: 0, avg_price: 0, regions: 0 },
      users: { total: 0, today_new: 0 },
      comments: { total: 0, today_new: 0, avg_rating: 0 },
      region_distribution: [],
      price_distribution: [],
      recent_houses: [],
    });

    const regionMax = computed(() =>
      Math.max(1, ...overview.value.region_distribution.map(r => r.value)));
    const priceMax = computed(() =>
      Math.max(1, ...overview.value.price_distribution.map(p => p.value)));

    function barWidth(value, max) {
      const pct = max ? Math.round((value / max) * 100) : 0;
      return Math.max(pct, value > 0 ? 3 : 0) + '%';
    }

    async function loadOverview() {
      try {
        const res = await api.get('/admin/overview');
        if (res.status === 401 || res.status === 403) return handleAuthError();
        if (!res.ok) return;
        const data = await res.json();
        overview.value = Object.assign({}, overview.value, data);
      } catch (e) { /* 忽略网络抖动 */ }
    }

    function handleAuthError() {
      notify('登录已过期，请重新登录', true);
      doLogout();
    }

    /* ---------------- 房源 ---------------- */
    const houses = ref([]);
    const houseTotal = ref(0);
    const housePage = ref(1);
    const housePageSize = ref(pageSize);
    const housesLoading = ref(false);
    const houseQuery = ref({ keyword: '', region: '' });
    const selectedHouses = ref([]);
    const regions = ref([]);

    const housePages = computed(() =>
      Math.max(1, Math.ceil(houseTotal.value / housePageSize.value)) || 1);
    const allHousesSelected = computed(() =>
      houses.value.length > 0 && selectedHouses.value.length === houses.value.length);

    function toggleAllHouses() {
      selectedHouses.value = allHousesSelected.value
        ? [] : houses.value.map(h => h.id);
    }

    async function loadHouses() {
      housesLoading.value = true;
      try {
        const params = new URLSearchParams();
        params.append('page', housePage.value);
        params.append('page_size', housePageSize.value);
        if (houseQuery.value.keyword.trim()) params.append('keyword', houseQuery.value.keyword.trim());
        if (houseQuery.value.region) params.append('region', houseQuery.value.region);
        const res = await api.get('/houses?' + params.toString());
        const data = await res.json();
        houses.value = data.houses || data.items || [];
        houseTotal.value = data.total || 0;
        selectedHouses.value = [];
      } catch (e) {
        notify('房源列表加载失败', true);
      } finally {
        housesLoading.value = false;
      }
    }

    function reloadHouses() {
      housePage.value = 1;
      loadHouses();
    }

    function changeHousePage(delta) {
      const next = housePage.value + delta;
      if (next < 1 || next > housePages.value) return;
      housePage.value = next;
      loadHouses();
    }

    /** 统一的分页跳转：首页 / 尾页 / 输入页码都走这里 */
    const houseJump = ref('');
    function goHousePage(page) {
      const total = housePages.value;
      const next = Math.min(Math.max(1, Number(page) || 1), total);
      housePage.value = next;
      houseJump.value = '';
      loadHouses();
    }

    function jumpHousePage() {
      const next = parseInt(houseJump.value, 10);
      if (!next || next < 1 || next > housePages.value) {
        notify(`请输入 1 ~ ${housePages.value} 之间的页码`, true);
        return;
      }
      goHousePage(next);
    }

    async function loadRegions() {
      try {
        const res = await api.get('/admin/regions');
        const data = await res.json();
        regions.value = data.regions || [];
      } catch (e) { /* 下拉为空不影响主流程 */ }
    }

    async function deleteHouse(house) {
      if (!window.confirm(`确定删除房源 #${house.id}「${house.title}」？删除后向量库会同步清理。`)) return;
      const res = await api.del('/houses/' + house.id);
      if (res.ok) {
        notify('已删除');
        loadHouses();
      } else {
        notify('删除失败', true);
      }
    }

    async function batchDeleteHouses() {
      const count = selectedHouses.value.length;
      if (!count) return;
      if (!window.confirm(`确定删除选中的 ${count} 条房源？`)) return;
      const res = await api.post('/admin/houses/batch-delete', { ids: selectedHouses.value });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        notify(data.message || '已删除');
        loadHouses();
      } else {
        notify(data.detail || '批量删除失败', true);
      }
    }

    async function exportHouses() {
      const params = new URLSearchParams();
      if (houseQuery.value.keyword.trim()) params.append('keyword', houseQuery.value.keyword.trim());
      if (houseQuery.value.region) params.append('region', houseQuery.value.region);
      const res = await api.get('/admin/houses/export?' + params.toString());
      if (!res.ok) { notify('导出失败', true); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'houses_' + Date.now() + '.csv';
      link.click();
      URL.revokeObjectURL(url);
      notify('已导出当前筛选结果');
    }

    const importInput = ref(null);
    function triggerImport() { importInput.value && importInput.value.click(); }

    async function importHouses(event) {
      const file = event.target.files && event.target.files[0];
      event.target.value = '';
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      const res = await api.upload('/admin/houses/import', form);
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        notify(data.message || '导入完成');
        loadHouses();
      } else {
        notify(data.detail || '导入失败', true);
      }
    }

    /* ------------- 新增 / 编辑抽屉 ------------- */
    const houseFormVisible = ref(false);
    const savingHouse = ref(false);

    // 下拉选项：与库内已有写法保持一致，避免 "2室1厅" / "两室一厅" 混着来
    const houseTypeOptions = ['1室0厅', '1室1厅', '2室1厅', '2室2厅', '3室1厅', '3室2厅', '4室2厅', '5室2厅'];
    const directionOptions = ['南', '北', '东', '西', '南北', '东西', '东南', '西南', '东北', '西北', '东西北'];
    const decorationOptions = ['精装', '简装', '毛坯', '豪装'];
    const floorOptions = ['低楼层', '中楼层', '高楼层', '地下室'];
    const rentTypeOptions = ['二手房', '新房', '租房'];

    const emptyHouse = () => ({
      id: null, title: '', region: '', price: '', rooms: '', area: '',
      direction: '', decoration: '', floor: '', rent_type: '二手房',
      publish_date: '', build_year: '', address: '', block: '',
      image_url: '', description: '',
      // 地图找房与详情页周边都依赖这两个字段，新增房源必须能录入
      longitude: '', latitude: '',
    });
    const houseForm = ref(emptyHouse());

    /* ------------- 图库：可视化选图 + 上传 ------------- */
    const galleryImages = ref([]);
    const galleryOpen = ref(false);
    const galleryLoaded = ref(false);
    const imageInput = ref(null);

    async function loadGallery(force) {
      if (galleryLoaded.value && !force) return;
      try {
        const res = await api.get('/admin/house-images');
        const data = await res.json();
        galleryImages.value = data.items || [];
        galleryLoaded.value = true;
      } catch (e) { /* 图库加载失败不影响手动保存 */ }
    }

    function toggleGallery() {
      galleryOpen.value = !galleryOpen.value;
      if (galleryOpen.value) loadGallery();
    }

    function pickImage(img) {
      houseForm.value.image_url = img.url;
    }

    function imageName(url) {
      return url ? String(url).split('/').pop() : '';
    }

    function triggerImageUpload() {
      imageInput.value && imageInput.value.click();
    }

    async function uploadImage(event) {
      const file = event.target.files && event.target.files[0];
      event.target.value = '';
      if (!file) return;
      if (file.size > 5 * 1024 * 1024) {
        notify('图片不能超过 5MB', true);
        return;
      }
      const form = new FormData();
      form.append('file', file);
      notify('图片上传中…');
      const res = await api.upload('/admin/house-images', form);
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        await loadGallery(true);
        houseForm.value.image_url = data.url;
        notify('图片已上传并选中');
      } else {
        notify(data.detail || '上传失败', true);
      }
    }

    /* ------------- 位置：高德地图选点 ------------- */

    const mapSearchKw = ref('');
    const mapLoadingMap = ref(false);
    // 点选后是否把逆地理编码结果（区域 / 街道 / 小区）回填到表单
    const autoFillAddr = ref(true);
    let amapPromise = null;
    let mapInstance = null;
    let mapMarker = null;
    let geocoder = null;
    let geoToken = 0;

    /** 懒加载高德 JS API：只有打开编辑抽屉时才下载，首屏不为它付代价
     *  Key 与安全密钥由后端 /api/config/map 下发，源码里不出现密钥（仓库要公开） */
    function loadAMap() {
      if (window.AMap) return Promise.resolve(window.AMap);
      if (amapPromise) return amapPromise;
      amapPromise = fetch('/api/config/map')
        .then((r) => r.json())
        .then((cfg) => {
          if (!cfg || !cfg.key) throw new Error('地图 Key 未配置（.env 的 AMAP_JS_KEY）');
          window._AMapSecurityConfig = { securityJsCode: cfg.security_code || '' };
          return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'https://webapi.amap.com/maps?v=2.0&key=' + cfg.key
              + '&plugin=AMap.PlaceSearch,AMap.Geocoder,AMap.Scale';
            script.onload = () => resolve(window.AMap);
            script.onerror = () => { amapPromise = null; reject(new Error('AMap load failed')); };
            document.head.appendChild(script);
          });
        })
        .catch((e) => { amapPromise = null; throw e; });
      return amapPromise;
    }

    async function initLocationMap() {
      mapLoadingMap.value = true;
      try {
        const AMap = await loadAMap();
        await nextTick();
        const container = document.getElementById('adminHouseMap');
        if (!container) { mapLoadingMap.value = false; return; }

        const lng = parseFloat(houseForm.value.longitude);
        const lat = parseFloat(houseForm.value.latitude);
        const hasCoord = !Number.isNaN(lng) && !Number.isNaN(lat);
        const center = hasCoord ? [lng, lat] : [116.4074, 39.9042]; // 默认北京市中心

        destroyMap();
        mapInstance = new AMap.Map('adminHouseMap', {
          zoom: hasCoord ? 16 : 12,
          center,
          resizeEnable: true,
          viewMode: '2D',
        });
        mapInstance.addControl(new AMap.Scale());

        mapMarker = new AMap.Marker({
          position: center,
          draggable: true,
          cursor: 'move',
          map: hasCoord ? mapInstance : null,
        });

        // 点地图任意位置即落点，标记可拖动微调
        mapInstance.on('click', (e) => setCoord(e.lnglat.getLng(), e.lnglat.getLat()));
        mapMarker.on('dragend', (e) => setCoord(e.lnglat.getLng(), e.lnglat.getLat()));

        mapLoadingMap.value = false;
      } catch (e) {
        mapLoadingMap.value = false;
        notify('地图加载失败，可手动填写经纬度或稍后重试', true);
      }
    }

    function setCoord(lng, lat) {
      houseForm.value.longitude = Number(lng).toFixed(6);
      houseForm.value.latitude = Number(lat).toFixed(6);
      if (mapInstance && mapMarker) {
        mapMarker.setPosition([lng, lat]);
        mapMarker.setMap(mapInstance);
        mapInstance.setCenter([lng, lat]);
      }
      // 点选 / 拖拽落点后，自动反查地址并回填
      if (autoFillAddr.value) reverseGeocode(lng, lat);
    }

    /** 区域下拉里的写法是「朝阳区」这种全称，优先匹配回已有选项，匹配不上就原样保留 */
    function matchRegion(district) {
      const raw = (district || '').trim();
      if (!raw) return '';
      const list = regions.value || [];
      const hit = list.find(r => r.name === raw)
        || list.find(r => r.name && (raw.indexOf(r.name) === 0 || r.name.indexOf(raw) === 0));
      return hit ? hit.name : raw;
    }

    /** 逆地理编码：经纬度 → 区域 / 街道乡镇 / 小区（或最近 POI） */
    function reverseGeocode(lng, lat) {
      if (!window.AMap) return;
      const token = ++geoToken;
      if (!geocoder) {
        geocoder = new window.AMap.Geocoder({ radius: 500, extensions: 'all' });
      }
      geocoder.getAddress([lng, lat], (status, result) => {
        // 连续点选时只认最后一次请求，避免慢响应覆盖新结果
        if (token !== geoToken) return;
        const regeo = status === 'complete' && result && result.regeocode;
        if (!regeo) {
          notify('这个点没识别出地址，可手动填写', true);
          return;
        }
        const comp = regeo.addressComponent || {};
        const region = matchRegion(comp.district);
        const street = (comp.township || '').trim();
        const poi = (regeo.pois && regeo.pois[0] && regeo.pois[0].name)
          || (comp.neighborhood && comp.neighborhood.name)
          || (comp.building && comp.building.name)
          || '';

        if (region) houseForm.value.region = region;
        if (street) houseForm.value.block = street;
        if (poi) houseForm.value.address = poi;

        const parts = [region, street, poi].filter(Boolean);
        notify(parts.length ? '已识别：' + parts.join(' · ') : '这个点没识别出地址，可手动填写',
          !parts.length);
      });
    }

    function searchLocation() {
      const keyword = (mapSearchKw.value || '').trim();
      if (!keyword) { notify('先输入要搜索的小区或商圈', true); return; }
      if (!window.AMap || !mapInstance) { notify('地图还没准备好，请稍等', true); return; }

      const placeSearch = new window.AMap.PlaceSearch({ pageSize: 1, city: '北京', citylimit: true });
      placeSearch.search(keyword, (status, result) => {
        const poi = result && result.poiList && result.poiList.pois && result.poiList.pois[0];
        if (status === 'complete' && poi) {
          setCoord(poi.location.lng, poi.location.lat);
          mapInstance.setZoom(16);
          if (!houseForm.value.address) houseForm.value.address = poi.name;
          notify('已定位到 ' + poi.name);
        } else {
          notify('没找到这个地点，换个更具体的名字试试', true);
        }
      });
    }

    /** 用「区域 + 街道 + 小区」拼一个地址去搜索，省得手动输入 */
    function locateByAddress() {
      const parts = [houseForm.value.region, houseForm.value.block, houseForm.value.address]
        .filter(Boolean);
      if (!parts.length) { notify('先填写区域或小区名', true); return; }
      mapSearchKw.value = parts.join('');
      searchLocation();
    }

    function clearCoord() {
      houseForm.value.longitude = '';
      houseForm.value.latitude = '';
      if (mapMarker) mapMarker.setMap(null);
    }

    function destroyMap() {
      if (mapInstance) {
        mapInstance.destroy();
        mapInstance = null;
        mapMarker = null;
      }
    }

    function closeHouseForm() {
      houseFormVisible.value = false;
      galleryOpen.value = false;
      destroyMap();
    }

    function openHouseForm(house) {
      if (house) {
        houseForm.value = Object.assign(emptyHouse(), {
          id: house.id, title: house.title || '', region: house.region || '',
          price: house.price || '', rooms: house.rooms || '', area: house.area || '',
          direction: house.direction || '', decoration: house.decoration || '',
          floor: house.floor || '', rent_type: house.rent_type || '二手房',
          publish_date: house.publish_date || '', build_year: house.build_year || '',
          address: house.address || '', block: house.block || '',
          image_url: house.image_url || '', description: house.description || '',
          longitude: house.longitude || '', latitude: house.latitude || '',
        });
      } else {
        houseForm.value = emptyHouse();
      }
      galleryOpen.value = false;
      mapSearchKw.value = '';
      houseFormVisible.value = true;
      loadGallery();
      nextTick(() => initLocationMap());
    }

    async function saveHouse() {
      const f = houseForm.value;
      if (!f.title.trim() || !f.region.trim() || !f.price.trim()) {
        notify('标题、区域、总价为必填项', true);
        return;
      }
      if (!f.longitude || !f.latitude) {
        const ok = window.confirm(
          '还没设置房源坐标：保存后不会出现在「地图找房」，详情页也拿不到周边配套。\n仍要保存吗？');
        if (!ok) return;
      }
      const payload = {};
      Object.keys(f).forEach(key => {
        if (key === 'id') return;
        const value = typeof f[key] === 'string' ? f[key].trim() : f[key];
        if (value !== '') payload[key] = value;
      });
      savingHouse.value = true;
      try {
        const res = f.id
          ? await api.put('/houses/' + f.id, payload)
          : await api.post('/houses', payload);
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          notify(f.id ? '已保存修改' : '已新增房源');
          houseFormVisible.value = false;
          loadHouses();
        } else {
          notify(data.detail ? String(JSON.stringify(data.detail)) : '保存失败', true);
        }
      } finally {
        savingHouse.value = false;
      }
    }

    /* ---------------- 用户 ---------------- */
    const users = ref([]);
    const userTotal = ref(0);
    const userPage = ref(1);
    const userQuery = ref({ keyword: '' });
    const userPages = computed(() =>
      Math.max(1, Math.ceil(userTotal.value / pageSize)) || 1);

    async function loadUsers(page) {
      userPage.value = Math.max(1, page || 1);
      try {
        const params = new URLSearchParams();
        params.append('page', userPage.value);
        params.append('page_size', pageSize);
        if (userQuery.value.keyword.trim()) params.append('keyword', userQuery.value.keyword.trim());
        const res = await api.get('/admin/users?' + params.toString());
        const data = await res.json();
        users.value = data.items || [];
        userTotal.value = data.total || 0;
      } catch (e) {
        notify('用户列表加载失败', true);
      }
    }

    /** 用户分页跳转 */
    const userJump = ref('');
    function jumpUserPage() {
      const next = parseInt(userJump.value, 10);
      if (!next || next < 1 || next > userPages.value) {
        notify(`请输入 1 ~ ${userPages.value} 之间的页码`, true);
        return;
      }
      userJump.value = '';
      loadUsers(next);
    }

    const resetUser = ref(null);
    const resetPassword = ref('');
    const resetting = ref(false);

    function openResetPwd(user) {
      resetUser.value = user;
      resetPassword.value = '';
    }

    async function submitResetPassword() {
      if (resetPassword.value.trim().length < 6) {
        notify('新密码至少 6 位', true);
        return;
      }
      resetting.value = true;
      try {
        const res = await api.post(
          '/admin/users/' + resetUser.value.id + '/reset-password',
          { new_password: resetPassword.value.trim() });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          notify(data.message || '密码已重置');
          resetUser.value = null;
        } else {
          notify(data.detail || '重置失败', true);
        }
      } finally {
        resetting.value = false;
      }
    }

    async function deleteUser(user) {
      if (!window.confirm(`确定删除用户「${user.name}」？该操作不可恢复。`)) return;
      const res = await api.del('/admin/users/' + user.id);
      if (res.ok) {
        notify('用户已删除');
        loadUsers(userPage.value);
      } else {
        notify('删除失败', true);
      }
    }

    /* ---------------- 评论 ---------------- */
    const comments = ref([]);
    const commentTotal = ref(0);
    const commentPage = ref(1);
    const commentQuery = ref({ keyword: '', house_id: '', id: '', sentiment: '' });
    const selectedComments = ref([]);
    const commentPages = computed(() =>
      Math.max(1, Math.ceil(commentTotal.value / pageSize)) || 1);
    const allCommentsSelected = computed(() =>
      comments.value.length > 0 && selectedComments.value.length === comments.value.length);

    function toggleAllComments() {
      selectedComments.value = allCommentsSelected.value
        ? [] : comments.value.map(c => c.id);
    }

    async function loadComments(page) {
      const idText = String(commentQuery.value.id || '').trim();
      const houseText = String(commentQuery.value.house_id || '').trim();
      if (idText && !/^\d+$/.test(idText)) { notify('评论 ID 只能填数字', true); return; }
      if (houseText && !/^\d+$/.test(houseText)) { notify('房源 ID 只能填数字', true); return; }

      commentPage.value = Math.max(1, page || 1);
      try {
        const params = new URLSearchParams();
        params.append('page', commentPage.value);
        params.append('page_size', pageSize);
        if (commentQuery.value.keyword.trim()) params.append('keyword', commentQuery.value.keyword.trim());
        if (houseText) params.append('house_id', houseText);
        if (idText) params.append('comment_id', idText);
        if (commentQuery.value.sentiment) params.append('sentiment', commentQuery.value.sentiment);
        const res = await api.get('/admin/comments?' + params.toString());
        const data = await res.json();
        comments.value = data.items || [];
        commentTotal.value = data.total || 0;
        selectedComments.value = [];
      } catch (e) {
        notify('评论列表加载失败', true);
      }
    }

    /* 评论详情弹窗：完整内容 + 房源信息 + 情感标签 */
    const commentDetail = ref(null);
    const commentHouse = ref(null);
    const commentHouseLoading = ref(false);

    function sentimentClass(sentiment) {
      if (sentiment === '积极') return 'pos';
      if (sentiment === '消极') return 'neg';
      return '';
    }

    async function openCommentDetail(comment) {
      commentDetail.value = comment;
      commentHouse.value = null;
      commentHouseLoading.value = !!(comment && comment.house_id);
      if (!comment || !comment.house_id) return;
      try {
        const res = await api.get('/houses/' + comment.house_id);
        const data = res.ok ? await res.json() : null;
        // 连点不同评论时，只保留最后一次的结果
        if (commentDetail.value && commentDetail.value.id === comment.id) commentHouse.value = data;
      } catch (e) {
        commentHouse.value = null;
      } finally {
        commentHouseLoading.value = false;
      }
    }

    function closeCommentDetail() {
      commentDetail.value = null;
      commentHouse.value = null;
      commentHouseLoading.value = false;
    }

    /** 详情里点「只看该房源评论」→ 回列表按房源筛选 */
    function filterCommentsByHouse(houseId) {
      if (!houseId) return;
      closeCommentDetail();
      commentQuery.value.id = '';
      commentQuery.value.house_id = String(houseId);
      loadComments(1);
    }

    /** 评论分页跳转 */
    const commentJump = ref('');
    function jumpCommentPage() {
      const next = parseInt(commentJump.value, 10);
      if (!next || next < 1 || next > commentPages.value) {
        notify(`请输入 1 ~ ${commentPages.value} 之间的页码`, true);
        return;
      }
      commentJump.value = '';
      loadComments(next);
    }

    async function deleteComment(comment) {
      if (!window.confirm('确定删除这条评论？')) return;
      const res = await api.del('/admin/comments/' + comment.id);
      if (res.ok) {
        notify('评论已删除');
        if (commentDetail.value && commentDetail.value.id === comment.id) closeCommentDetail();
        loadComments(commentPage.value);
      } else {
        notify('删除失败', true);
      }
    }

    async function batchDeleteComments() {
      const count = selectedComments.value.length;
      if (!count) return;
      if (!window.confirm(`确定删除选中的 ${count} 条评论？`)) return;
      const res = await api.post('/admin/comments/batch-delete', { ids: selectedComments.value });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        notify(data.message || '已删除');
        loadComments(commentPage.value);
      } else {
        notify(data.detail || '批量删除失败', true);
      }
    }

    /** 补算情感标签：只补未打标的，按当前筛选条件一次处理最多 500 条 */
    const commentReanalyzing = ref(false);
    async function reanalyzeSentiment() {
      if (commentReanalyzing.value) return;
      commentReanalyzing.value = true;
      try {
        const params = new URLSearchParams();
        const kw = commentQuery.value.keyword.trim();
        const houseText = String(commentQuery.value.house_id || '').trim();
        const idText = String(commentQuery.value.id || '').trim();
        if (kw) params.append('keyword', kw);
        if (houseText) params.append('house_id', houseText);
        if (idText) params.append('comment_id', idText);
        params.append('limit', 500);
        const res = await api.post('/admin/comments/reanalyze?' + params.toString(), {});
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          notify(data.message || '情感标签已更新');
          loadComments(commentPage.value);
        } else {
          notify(data.detail || '补算失败', true);
        }
      } catch (e) {
        notify('补算失败', true);
      } finally {
        commentReanalyzing.value = false;
      }
    }

    /** 导出当前筛选的评论（含情感标签） */
    async function exportComments() {
      const params = new URLSearchParams();
      const kw = commentQuery.value.keyword.trim();
      const houseText = String(commentQuery.value.house_id || '').trim();
      const idText = String(commentQuery.value.id || '').trim();
      if (kw) params.append('keyword', kw);
      if (houseText) params.append('house_id', houseText);
      if (idText) params.append('comment_id', idText);
      if (commentQuery.value.sentiment) params.append('sentiment', commentQuery.value.sentiment);
      const res = await api.get('/admin/comments/export?' + params.toString());
      if (!res.ok) { notify('导出失败', true); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'comments_' + Date.now() + '.csv';
      link.click();
      URL.revokeObjectURL(url);
      notify('已导出当前筛选结果');
    }

    /* ---------------- 知识库 ---------------- */
    const docs = ref([]);
    const docTotal = ref(0);
    const docPage = ref(1);
    const indexing = ref(false);
    const uploadInput = ref(null);
    const docPages = computed(() =>
      Math.max(1, Math.ceil(docTotal.value / pageSize)) || 1);

    async function loadDocs(page) {
      docPage.value = Math.max(1, page || 1);
      try {
        const params = new URLSearchParams();
        params.append('page', docPage.value);
        params.append('page_size', pageSize);
        const res = await api.get('/knowledge/documents?' + params.toString());
        const data = await res.json();
        docs.value = data.items || [];
        docTotal.value = data.total || 0;
      } catch (e) {
        notify('知识库加载失败', true);
      }
    }

    /** 知识库分页跳转 */
    const docJump = ref('');
    function jumpDocPage() {
      const next = parseInt(docJump.value, 10);
      if (!next || next < 1 || next > docPages.value) {
        notify(`请输入 1 ~ ${docPages.value} 之间的页码`, true);
        return;
      }
      docJump.value = '';
      loadDocs(next);
    }

    function triggerUpload() { uploadInput.value && uploadInput.value.click(); }

    async function uploadDoc(event) {
      const file = event.target.files && event.target.files[0];
      event.target.value = '';
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      notify('正在上传并建立索引…');
      const res = await api.upload('/knowledge/upload', form);
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        notify('上传成功，已建立向量索引');
        loadDocs(1);
      } else {
        notify(data.detail || '上传失败', true);
      }
    }

    async function deleteDoc(doc) {
      if (!window.confirm(`确定删除文档「${doc.filename || doc.doc_id}」及其向量？`)) return;
      const res = await api.del('/knowledge/documents/' + doc.doc_id);
      if (res.ok) {
        notify('文档已删除');
        loadDocs(docPage.value);
      } else {
        notify('删除失败', true);
      }
    }

    async function rebuildIndex() {
      if (!window.confirm('重建房源向量索引会重新入向量库，耗时约数十秒，确定继续？')) return;
      indexing.value = true;
      try {
        const res = await api.post('/knowledge/index-houses?limit=1000');
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          notify(`索引重建完成：${data.indexed ?? data.count ?? '已完成'}`);
        } else {
          notify(data.detail || '重建失败', true);
        }
      } finally {
        indexing.value = false;
      }
    }

    /* ---------------- 初始化 ---------------- */
    onMounted(async () => {
      try {
        // 后台没有独立登录态时，直接复用前台的管理员身份（单点登录）
        if (!token.value && !(await tryFrontendSso())) return;
        // 校验本地登录态，过期/被踢掉就回登录页，避免先闪一下后台
        const res = await api.get('/user/admin/me');
        if (!res.ok) { doLogout(); return; }
        switchView('overview');
        loadRegions();
      } catch (e) {
        doLogout();
      } finally {
        authChecking.value = false;
      }
    });

    return {
      token, admin, remember, authChecking, loginForm, loginError, logging, doLogin, doLogout,
      view, viewMeta, switchView, refreshCurrent,
      overview, regionMax, priceMax, barWidth,
      houses, houseTotal, housePage, housePages, housePageSize, housesLoading,
      houseQuery, reloadHouses, changeHousePage, selectedHouses,
      houseJump, goHousePage, jumpHousePage,
      allHousesSelected, toggleAllHouses, loadHouses,
      deleteHouse, batchDeleteHouses, exportHouses, triggerImport, importHouses, importInput,
      houseForm, houseFormVisible, savingHouse, openHouseForm, saveHouse, closeHouseForm,
      houseTypeOptions, directionOptions, decorationOptions, floorOptions, rentTypeOptions,
      galleryImages, galleryOpen, imageInput, toggleGallery, pickImage, imageName,
      triggerImageUpload, uploadImage,
      mapSearchKw, mapLoadingMap, autoFillAddr, searchLocation, locateByAddress, clearCoord,
      users, userTotal, userPage, userPages, userQuery, loadUsers, userJump, jumpUserPage,
      resetUser, resetPassword, resetting, openResetPwd, submitResetPassword, deleteUser,
      comments, commentTotal, commentPage, commentPages, commentQuery,
      commentJump, jumpCommentPage,
      commentDetail, commentHouse, commentHouseLoading,
      openCommentDetail, closeCommentDetail, filterCommentsByHouse, sentimentClass,
      selectedComments, allCommentsSelected, toggleAllComments,
      loadComments, deleteComment, batchDeleteComments,
      commentReanalyzing, reanalyzeSentiment, exportComments,
      docs, docTotal, docPage, docPages, loadDocs, docJump, jumpDocPage,
      triggerUpload, uploadDoc, uploadInput,
      deleteDoc, rebuildIndex, indexing,
      regions, pageSize, formatTime, toast,
    };
  },
}).mount('#app');
