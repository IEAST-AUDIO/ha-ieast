# HA 首次使用与 iEAST 集成测试指南

写给第一次接触 Home Assistant 的同事。跟着做大约 15 分钟，能跑通"部署 → 添加设备 → 控制播放 → 开发者工作台"全流程。

> Home Assistant（下称 HA）是一个开源的智能家居中控，跑在你们 3.75 服务器上
> （Docker 容器），浏览器打开 **http://192.168.3.75:8123** 就是它的操作界面。

---

## 第 0 步：确认前提

1. 服务器上已预置好集成文件（我已传好，无需再传）：
   - `/tmp/ieast_deploy`（集成本体，19 个文件）
   - `/tmp/ieast_remote_install.sh`（安装脚本）
2. 你的电脑能打开 http://192.168.3.75:8123 并登录（有账号；没有就点页面下方"创建账号"，第一次登录才需要）
3. 至少一台 iEAST 设备和服务器在**同一个局域网**（192.168.3.x 网段），知道它的 IP（路由器后台或 iEAST App 里看）

## 第 1 步：安装集成（一条命令，输一次 sudo 密码）

在**你的 Windows 电脑**上打开 PowerShell，执行：

```powershell
ssh -t -i $env:USERPROFILE\.ssh\id_ed25519 ieast@192.168.3.75 "sudo bash /tmp/ieast_remote_install.sh --restart"
```

提示 `Password:` 时输入 ieast 用户的 sudo 密码。脚本会自动：
1. 把集成装进 `/opt/homeassistant/custom_components/ieast`
2. 在 HA 容器内做一次语法校验（看到 `SYNTAX OK: N files` 即通过）
3. 重启 HA 容器（约 1-2 分钟生效）

看到 `== 部署完成 ==` 就可以继续。

## 第 2 步：添加 iEAST 集成

1. 浏览器打开 http://192.168.3.75:8123 并登录
2. 左侧边栏点 **设置**（Settings）→ **设备与服务**（Devices & Services）
3. 右下角点 **+ 添加集成**（Add Integration）
4. 搜索框输入 **ieast**，点 **iEAST Audio**
5. 弹窗里只填一项：**主机地址**——填 iEAST 设备的 IP，点提交
6. 成功后会弹出设备卡片：显示设备名/型号/固件，下面挂着它的全部实体

> 如果列表里搜不到 ieast：说明 HA 还没重启完，等 1 分钟再试；
> 还不行看第 6 步日志排查。

## 第 3 步：基本功能验证（5 分钟）

在设备卡片里直接操作这些实体，每项都应立即生效：

| 实体 | 操作 | 预期 |
|------|------|------|
| 播放器 | 音量条拖动 | 设备音量变化，几秒内回显 |
| 播放器 | 暂停/播放 | 设备跟随 |
| 播放器 | 音源下拉 | 切蓝牙/AUX/光纤，设备屏或 App 可见切换 |
| 播放器 | 声音模式 | 切 EQ 预设（Flat/Rock…），音色变化 |
| 睡眠定时 | 设 30 分钟 | 设备 30 分钟后关机 |
| 预设 1-N | 点击 | 触发设备端预设（TCP 通道） |

## 第 4 步：开发者工作台（重点）

### 4.1 导入控制台脚本
1. 服务器上编辑 `/opt/homeassistant/configuration.yaml`（需要 sudo，见第 5 步的在线编辑方式），把 `G:\BP10_code\ha-ieast\examples\scripts_console.yaml` 的内容合并到 `script:` 段下
2. 开发者工具 → YAML → 重载脚本（Script）

### 4.2 导入开发者工作台面板
1. 左侧边栏 → 概览 → 右上角 **⋮ → 编辑仪表盘 → ⋮ → 原始编辑器**
2. 把 `G:\BP10_code\ha-ieast\examples\lovelace_developer.yaml` 内容粘贴进去（把里面的 `media_player.room1` 等替换成你的实际实体 ID——设备卡片里每个实体点开能看到 Entity ID）
3. 保存后侧边栏就有 5 个调试视图：设备取证 / 指令控制台 / DSP 工具 / 多房间测试 / 诊断速查

### 4.3 最有用的三个调试动作
在 **开发者工具 → 动作（Actions）** 里选动作、填 entity_id、执行，响应直接显示：

| 动作 | 用途 |
|------|------|
| `ieast.scan_device` | 一键取证：设备信息+DSP 能力+分组全部返回 |
| `ieast.send_http_command` | 任意 httpapi 命令直发（如 `getPlayerStatus`） |
| `ieast.dsp_diag` | DSP 诊断（DPST 运行态/错误码） |

## 第 5 步：多房间测试（两台以上设备时）

1. 每台设备都按第 2 步添加一遍
2. 开发者工具 → 动作 → `ieast.party_mode`，master 填客厅播放器 → 全屋齐播
3. 动作 `ieast.ungroup_all` → 全部恢复独立
4. 动作 `ieast.announce`，targets 选房间、message 填文字、tts_platform 填 `google_translate` → TTS 插播后自动恢复原状态

## 第 6 步：出问题了怎么办

| 现象 | 排查 |
|------|------|
| 添加集成里搜不到 ieast | HA 没重启成功：`ssh ieast@192.168.3.75` 后 `sudo docker restart <容器名>`；或看第 7 步日志找红字 |
| 添加时"无法连接设备" | 设备 IP 不对 / 设备和服务器不同网段 / 设备关机。先在电脑上 `python G:\BP10_code\ha-ieast\tools\ieast_probe.py <设备IP>` 探测 |
| 实体全部"不可用" | 设备掉线，恢复网络后 30 秒内自动恢复 |
| 音量能设但没反应 | 看设备是否处于蓝牙/AUX 模式（部分音源不接受网络音量指令） |
| DSP 实体没出现 | 正常——按机型探测挂载，非 BP10 机型只有标准 EQ；执行 `ieast.scan_device` 看 dsp_caps 字段确认探测结果 |
| 想看完整日志 | 第 7 步 |

## 第 7 步：日志与诊断

```bash
# 服务器上看 HA 日志里 iEAST 相关行
ssh ieast@192.168.3.75
sudo docker logs --tail 200 <容器名> 2>&1 | grep -i ieast
```

开调试级日志：编辑 `/opt/homeassistant/configuration.yaml`（root 权限）加入：

```yaml
logger:
  logs:
    custom_components.ieast: debug
```

或者不动配置文件：**设置 → 设备与服务 → iEAST Audio → ⋮ → 下载诊断**，
把诊断 JSON 发回来即可（已自动脱敏，含 DSP 探测结果与最后错误）。

## 附：日常更新集成

改了代码后重新部署：

```powershell
scp -r -i $env:USERPROFILE\.ssh\id_ed25519 G:\BP10_code\ha-ieast\custom_components\ieast ieast@192.168.3.75:/tmp/ieast_deploy
ssh -t -i $env:USERPROFILE\.ssh\id_ed25519 ieast@192.168.3.75 "sudo bash /tmp/ieast_remote_install.sh --restart"
```
