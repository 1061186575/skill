# home-h5 项目的重点事项

> 这个示例模板文件

## home-h5 项目技术栈与常用方法

### 技术栈

Vue 2, Vue Router, Vuex, Vant, Less, vRoute

### 常用方法
XXX

## home-h5 项目响应数据的处理不一样

源项目可能是通过 `code` 判断请求是否成功, response 示例:

```js
api.get(url, { id: 1 }).then(response => {
    // response 数据结构:
    // {
    //    "code": 100000,
    //    "msg": "成功",
    //    "data": {}
    // }
})
```

而 `home-h5` 项目没有 code 和 msg 字段, response 就是 data, 如果请求失败 response 一定是 null, 如果请求成功一定是一个对象, response 示例:

```js
api.get(url, { id: 1 }).then(response => {
    const data = response;
    if (!data) return; // 请求失败, 这里不用弹出错误提示, 底层会自动弹出错误提示
    // 请求成功
    console.log(data);
})
```

## 项目目录结构映射

| 源项目 (PC)                   | 适配目标项目 (H5)          | 描述     |
|----------------------------|----------------------|--------|
| client/src/router/index.js | client/src/router.js | 路由文件   |
| client/src/routes/AAA      | client/src/pages/BBB | XX     |
| client/src/routes/*        | client/src/pages/*   | 主要开发文件 |


## 其他注意事项
xxx
