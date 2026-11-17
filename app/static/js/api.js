/* =========================================
 * API 请求层：统一管理接口地址与请求封装
 * 用法：api.get('/houses') / api.post('/chat/ask', {...}) / api.del('/houses/favorites/'+id)
 * 鉴权：登录成功后调用 api.setAuthToken(token)，后续请求自动带上 Authorization 头
 *       （普通用户 Token 与管理员 Token 走同一套机制，差别只在 Token 里的 role）
 * ========================================= */
const API_BASE = '/api';

const api = {
  BASE: API_BASE,

  /** 当前登录 Token（未登录为空） */
  authToken: '',

  /** 设置 / 清除登录 Token */
  setAuthToken(token) {
    this.authToken = token || '';
  },

  /** 通用请求：自动附带登录 Token */
  request(path, options = {}) {
    const opts = Object.assign({}, options);
    const headers = Object.assign({}, opts.headers || {});
    if (this.authToken) {
      headers['Authorization'] = 'Bearer ' + this.authToken;
    }
    opts.headers = headers;
    return fetch(API_BASE + path, opts);
  },

  /** GET 请求 */
  get(path) {
    return this.request(path);
  },

  /** POST 请求（JSON body） */
  post(path, body) {
    return this.request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  },

  /** PUT 请求（JSON body） */
  put(path, body) {
    return this.request(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  },

  /** DELETE 请求 */
  del(path) {
    return this.request(path, { method: 'DELETE' });
  },

  /** 文件上传（multipart/form-data，交给浏览器自动补 boundary） */
  upload(path, formData) {
    return this.request(path, { method: 'POST', body: formData });
  },
};
