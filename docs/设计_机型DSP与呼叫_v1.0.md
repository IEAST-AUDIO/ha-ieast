# iEAST Audio-HA 设计：机型化 DSP 挂载 与 呼叫系统 v1.0

日期：2026-09-23
状态：**P1+P2 已实现**
- P1：§2.2 能力探测（参数级挂载）、§2.3 实体矩阵、§2.4 服务、§3.1 announce、§3.2 page
- P2：§2.4 中 SCH 方案库服务（list/upload/apply/delete）、PK 参数包对拷（pack.py）、立体声对（stereo_pair_create/remove）
- 面向开发者：diagnostics 诊断下载（脱敏）、`ieast.scan_device` 全量探测、开发者工作台 Lovelace（examples/lovelace_developer.yaml）、表单化指令控制台脚本（examples/scripts_console.yaml）
- P3 待做：从设备当前状态合成 128B 方案（DPE 3B 与校验算法待 MCU 侧确认）；PEQ 调音台专属卡片；voicemail 对讲（待固件接口）

> 实现说明（与原设计的差异）：
> - 机型档案采用"在线探测优先"（PEQC/DPQ60/DPST/PKI），不再依赖静态
>   `dsp_profiles/*.json` 匹配表——探测即真相，避免 project 字段命名不一致导致漏挂载
> - **参数级挂载（已实现）**：确认 BP10 家族后逐组 `DPQ<g>A&`（不全时逐项
>   `DPQ<g><i>&` 补查）枚举参数，`DspCaps.params` 只收录应答有效的 (组,项)->当前值；
>   实体仅为本字典中的参数创建，初值即查询值。A230D 形态（PEQC=10 但 DPU 全不支持）
>   只挂 PEQ，不出任何 DPU 实体
> - DPEA（Maxx 总开关）以 DPST(v3517+) 存在性为门控
> - 12 段 PEQ 编辑已可用（`ieast.peq_set/get/reset`），调音台卡片仍在 P3
> - 固件侧后续若提供 DSP 能力描述（如 getStatusEx 增加 DSP 字段或专用查询命令），
>   只需替换 `probe_dsp` 的数据来源，实体挂载层（caps.params 驱动）无需改动
依据协议：
- `BP10_PEQ_接口协议_v1.0.md`（12 段 PEQ，110P=12 段 / 215=10 段，物理值传输）
- `BP10_DSP_全参数接口协议_v1.1.md`（DPU 7 组 / SCH 方案库 / PK 量产参数包 / DPST 诊断）
- `linkplay/02_接口协议/网络API/IEAST/`（HTTP API V1.1 / TCP API V1.2 / UPnP V1.0）

---

## 0. 关键协议事实（设计的前提）

1. **DSP 命令走普通 HTTP 通道**：`http://<ip>/httpapi.asp?command=MCU+PAS+<CMD>&`
   A31 → UART 透传 → BP10 → I2C 写 DSP，应答（`PAS+...&`）原样随 HTTP 返回。
   即：现有 `IeastClient._request()` 直接可用，TCP 8899 只作为备选通道。
2. **机型能力可在线探测**：
   - `MCU+PAS+PEQC&` → `PAS+PEQCnn`，nn=段数（A450D=12 / A230D=10）
   - `MCU+PAS+DPST&` → DSP 状态诊断（v3517+），区分方案库有无
   - `MCU+PAS+PKI&` → family（1=110P，2=215）
   - `getStatusEx.project / priv_prj / hardware` → 机型识别
3. **不同机型 DSP 差异是协议内建的**：段数不同、SYS 段布局不同（110P 16B / 215 57B）、
   215 无 SCH 方案库；PK 导入强制核对 family，不一致拒绝写入。

## 1. 设计原则：借鉴 Sonos 的"骨架"，做 CI（工程安装）品牌的"血肉"

**从 Sonos 借鉴（用户已习惯的骨架）**：
- join/unjoin 分组模型、客厅为主机的全屋齐播
- 立体声对（Sonos stereo pair）→ 我们用 `multiroom:SlaveChannel`（0立体/1左/2右）
  两台同型号组 L/R，HA 按一个"立体声对设备"呈现
- 夜间模式/对白增强 → 映射到 MaxxVolume(dyn_range) / MaxxDialog
- 闹钟、睡眠定时、插播恢复（TTS announce）

**iEAST 自己的思路（Sonos 做不到/不做的）**：
1. **DSP 即场景**：房间级 DSP 配置可随场景切换——"电影"=MaxxDialog 中置增强+
   MaxxVolume 压限、"音乐"=Flat+Bass 增强、"夜间"=MaxxVolume 低动态+勿扰。
   Sonos 的 Night/Speech 是固定算法，我们是 7 组 DPU + 12 段 PEQ 的自由组合。
2. **调音方案库 HA 化**：SCH（9 音箱类型 × 9 槽位）在 HA 内存/取/换，
   调音师在 ProConsole/易调 调好 → HA 一键分发到全屋同型号房间。
   等价于"平民版 Trueplay"，但调音来自专业调音师而非手机扫频。
3. **量产参数包对拷**：金样机 `PKI/PKR` 导出 → HA 存档 → 新装机器 `PKB/PKW/PKC/PKF`
   导入，工程商换机/加装零调音。family 校验由协议保证不串机型。
4. **音源矩阵**：每房间 AUX/光纤/蓝牙/USB 独立输入 + 分组广播，
   不加矩阵硬件即得 zone matrix（Sonos 需 Arc/Port 才有 HDMI/line-in 矩阵能力）。
5. **工程级自动化**：TRIGGER IN 高/低电平开关机联动投影幕、电动幕布、
   功放时序；RS232/MCU 指令直通——全宅集成商的刚需。

## 2. 机型化 DSP 挂载（架构）

### 2.1 机型档案注册表 `dsp_profiles/<model>.json`

```json
{
  "model": "A450D",
  "match": { "project": ["A450D", "a450d"], "hardware": ["NPCA110P"] },
  "family": 1,
  "dsp": {
    "peq_bands": "probe",          // 优先 PEQC 在线探测, 失败回退 12
    "dpu_groups": [0,1,2,3,4,5,6],
    "scheme_library": true,         // SCH
    "pack_io": true,                // PK
    "speaker_types": 9,
    "friendly_params": ["g0i0","g1i0","g2i0","g6i0"]   // 第一批友好开放项
  }
}
```

A230D 对应 `family:2 / scheme_library:false / peq_bands:"probe"(10)`；
纯 Linkplay 标准机型（eAMP/ePlay/M30/M50 等，无 BP10 级 DSP）回退
`linkplay_std` 档案：只有 EQOn/EQOff/EQGetList/EQLoad（已实现的声音模式）。

### 2.2 挂载流程（setup 时执行）

```
getStatusEx(project/hardware) → 匹配档案
  → PEQC 探测段数、DPST 探测方案库、PKI 探测 family
  → 按"探测∩档案"挂载实体, 不支持的机型一个多余实体都不出
```

### 2.3 实体挂载矩阵（避免实体爆炸）

| 能力 | 实体形态 | 数量 |
|---|---|---|
| 音箱类型 / 预设音效(SPEQ) | select | 2 |
| MaxxBass 强度 / MaxxTreble 强度 / Balance | number | 3 |
| Maxx3D / Maxx 总开关(DPEA) / Leveler | switch | ≤3 |
| PEQ 恢复默认 / DSP 诊断 | button | 2 |
| **PEQ 12 段编辑** | **不走实体**，service + 专属卡片 | 0 |
| SCH 方案 / PK 参数包 | service | 0 |

PEQ 12 段 × 4 参数用实体表达会铺满 48 个滑条，不可维护。设计为：

- `ieast.peq_get` / `ieast.peq_set`（band, freq, gain, q, type）/ `ieast.peq_reset`
- 当前 PEQ 以 media_player 属性 JSON + 诊断 sensor 暴露
- v1.1 自定义卡片"PEQ 调音台"（12 列滑条 + 曲线图），数据源即上述 service

### 2.4 新增服务（DSP 族）

| 服务 | 协议映射 | 说明 |
|---|---|---|
| `ieast.dsp_param_set/get` | DPS/DPQ | 组号+项号或友好名(bass_boost 等) |
| `ieast.dsp_group_reset` | DPR | 复位某组 |
| `ieast.peq_set/get/reset` | PEQF/Q/G/T/R/D | 物理值(Hz/dB/Q) |
| `ieast.dsp_scheme_save/load/delete/list` | DPB/DPU/DPC/DPA/DPX/DPL | 方案库管理 |
| `ieast.dsp_pack_export/import` | PKI/PKR / PKB/PKW/PKC/PKF | 参数包对拷（存 HA config 目录） |
| `ieast.dsp_diag` | DPST/DPEA | 诊断（带响应返回） |

## 3. 呼叫系统设计（可实现，分三层）

### 3.1 L1 插播呼叫（全机型，当天可做）

`ieast.announce`：快照 → (可选自动入组) → 播 URL/TTS → 恢复快照。

```yaml
- service: ieast.announce
  data:
    targets: [media_player.living_room, media_player.study]
    message: "晚餐好了"          # 走 HA TTS 生成 URL
    group: true                  # 未分组的先临时加入第一目标
    restore: true
```

门铃、快递、老人呼叫按钮（无线按钮 → HA）都属于这层。

### 3.2 L2 实时广播/喊话（paging，全机型，核心差异化）

原理：**主机的 AUX 输入 + 分组 = 广播矩阵**。呼叫台/麦克风接主机 AUX：

```
ieast.page:
  master: media_player.hall        # AUX 接呼叫麦的房间
  targets: all | [rooms]
  # 动作: targets 临时 join master → master 切 line-in → 实时转播
  # 挂断: unjoin → 恢复各房间原音源/音量(快照)
```

协议依据：`setPlayerCmd:switchmode:line-in` + `ConnectMasterAp` 组网均为既有命令；
多房间同步延迟约 200-500ms，对喊话/广播完全可接受。
适用：店铺/民宿/办公室呼叫、全宅喊话、门禁对讲的外放侧。

### 3.3 L3 双向对讲（需要外部条件，列为候选）

设备本身无麦克风、无网络音频输入 API。可行路径：

- **HA 语音终端做呼叫麦**：Wyoming Satellite / HA Voice PE 拾音 →
  HA 转流（icecast/HTTP stream）→ `play_media` 推给目标房间 = 单向"呼叫→房间"；
  回话用同一终端在目标房间旁听（近似对讲）。
- **固件配合路线（需与平台组确认）**：wiimu 模式表里有 `60 = Voice mail`
  通道（iEAST App 留言/对讲即走此通道），若开放 HTTP 触发接口，则 App/HA
  推送语音消息到设备即原生对讲留言。**建议向 wiimu 平台要 voicemail 的
  httpapi 触发命令**，拿到即做。

结论：L1/L2 用现有协议即可完整落地；L3 的"留言对讲"差一个固件接口确认。

## 4. 实施顺序建议

1. **P1**：DSP 档案注册表 + PEQ/友好参数 service 族 + PEQC/DPST/PKI 探测挂载
   （HTTP 通道，改动集中在 api.py + 新增 dsp.py 平台）
2. **P1**：`ieast.announce`（快照服务已有，组装即得）
3. **P2**：`ieast.page`（入组+切源+恢复的编排）、立体声对实体
4. **P2**：SCH/PK 方案库与参数包对拷服务（工程商功能）
5. **P3**：PEQ 调音台自定义卡片、voicemail 对讲（待固件接口）
