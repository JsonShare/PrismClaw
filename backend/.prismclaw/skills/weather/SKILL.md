---
name: weather
description: 天气查询。获取当前天气和未来预报。适用于查询天气、温度、湿度、风速、气象、出行穿衣、是否下雨、带伞等场景，支持中文城市和国际城市。
homepage: https://wttr.in/:help
metadata: {"prismclaw":{"emoji":"🌤️","requires":{"bins":["curl"]}}}
name_zh: 天气查询
category: 信息检索
---

# Weather

## wttr.in（主服务）

紧凑格式（推荐）：
```bash
curl -s --noproxy "*" "wttr.in/Beijing?format=%l:+%c+%t+%h+%w&lang=zh"
# 输出: Beijing: ☀️  +32°C 24% ↖6km/h
```

仅当前天气：
```bash
curl -s --noproxy "*" "wttr.in/Shanghai?format=3&lang=zh"
# 输出: Shanghai: ⛅️ +28°C
```

完整预报（今日）：
```bash
curl -s --noproxy "*" "wttr.in/Beijing?lang=zh&1&T"
```

格式代码: `%c` 天气状况 · `%t` 温度 · `%h` 湿度 · `%w` 风速 · `%l` 地名 · `%m` 月相

提示：
- 城市名用英文或拼音: `wttr.in/Beijing`、`wttr.in/Shanghai`、`wttr.in/Guangzhou`
- 中文输出: 加 `&lang=zh`
- 机场代码: `wttr.in/PEK`（北京首都）、`wttr.in/PVG`（上海浦东）
- 单位: `?m` 公制 `?u` 英制
- 仅今天: `?1` · 仅当前: `?0`
- URL 编码空格: `wttr.in/New+York`

## Open-Meteo（备选，JSON）

适合程序化处理：
```bash
curl -s --noproxy "*" "https://api.open-meteo.com/v1/forecast?latitude=39.9&longitude=116.4&current_weather=true"
```

常用城市坐标：
- 北京: latitude=39.9, longitude=116.4
- 上海: latitude=31.2, longitude=121.5
- 广州: latitude=23.1, longitude=113.3
- 深圳: latitude=22.5, longitude=114.1

返回 JSON，包含 temperature、windspeed、weathercode 等字段。

文档: https://open-meteo.com/en/docs

## 代理环境提示

若系统配置了 HTTP_PROXY 但代理未运行，curl 会报 exit code 7（Connection refused）。此时加 `--noproxy "*"` 绕过代理直连即可。
