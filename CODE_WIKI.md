# BidKingHelper - Code Wiki

## 1. 项目概述

BidKingHelper 是竞拍之王（BidKing）游戏的对局信息自动化抓取与分析工具。项目分为两个核心部分：

- **数据采集层**（Python）：通过监听网络流获取房间号，扫描游戏进程内存提取对局日志JSON
- **数据分析与展示层**（Tauri 客户端，规划中）：接收JSON数据，提取关键信息，实时展示对局统计

### 1.1 ai agent已完成，待写入CODE_WIKI
#### 1.1.1. Rust 后端 — pipe_listener.rs
启动后台线程监听 \\.\pipe\bidking_log Named Pipe
接收 Python 推送的 JSON 行消息
解析为 PipeMessage 结构体，通过 app.emit("game-event", &msg) 推送到前端
支持客户端断开重连
#### 1.1.2. React 前端 — App.tsx
品质标签 UI：7行（总数/白/绿/蓝/紫/金/红），每行3个可编辑输入框：

总格数（TotalHitBoxIndex）— 自动填充或手动输入
平均格数（AllHitItemAvgBoxIndex）— 自动填充或手动输入
物品数量（HitItemIndex）— 自动计算 round(gridCount / avgGrid) 或手动输入
估算算法（O(maxItems) 优化）：

给定 avgGrid = A（如3.15），计算 A_int = round(A × 100) = 315
对每个候选件数 n（1 到 maxGrid）：
  G_min = ceil(A_int × n / 100)    // 最小可能总格数
  G_max = floor(((A_int + 1) × n - 1) / 100)  // 最大可能总格数
  若 G_min ≤ G_max 且 G_min ≥ n 且 G_max ≤ maxGrid：
    → 件数 n 有效，总格数范围 [G_min, G_max]

给定 avgGrid = A（如3.15），计算 A_int = round(A × 100) = 315
对每个候选件数 n（1 到 maxGrid）：
  G_min = ceil(A_int × n / 100)    // 最小可能总格数
  G_max = floor(((A_int + 1) × n - 1) / 100)  // 最大可能总格数
  若 G_min ≤ G_max 且 G_min ≥ n 且 G_max ≤ maxGrid：
    → 件数 n 有效，总格数范围 [G_min, G_max]
默认推算到总格数150，玩家可填入最大值（不超过500）。

#### 1.1.3. monitor_ram.py 修改
新增 pipe_connect() / pipe_send() 函数（使用 pywin32 的 win32file.CreateFile / WriteFile）
save_json_log() 保存文件后自动调用 pipe_send(event_type, room_id, parsed)
启动时尝试连接 Named Pipe，若客户端未启动则优雅降级（仅保存文件）
写入失败自动重连

#### 1.1.4. 运行方式：
##### 1.1.4.1. 启动 Tauri 客户端
cd d:\Projects\BidKingHelper\bidking-client
npx tauri dev
或直接运行已编译的 exe:
src-tauri\target\debug\bidking-client.exe

##### 1.1.4.2. 启动 Python 采集脚本
cd d:\Projects\BidKingHelper
pip install pywin32
python monitor_ram.py

# 1. 启动 Tauri 客户端
cd d:\Projects\BidKingHelper\bidking-client
npx tauri dev
# 或直接运行已编译的 exe:
# src-tauri\target\debug\bidking-client.exe

# 2. 启动 Python 采集脚本
cd d:\Projects\BidKingHelper
pip install pywin32
python monitor_ram.py
注意：Rust 编译需使用 nightly 工具链（rustup run nightly cargo build），stable 1.95.0 在此环境下 build script 有进程创建 bug。

## 2. 项目结构

```
BidKingHelper/
├── monitor_ram.py              # 核心采集脚本（Python）
├── requirements.txt            # Python 依赖
├── CODE_WIKI.md
├── DataDefinitions/			# 游戏数据定义
│   ├── item_prices.csv         # 物品价格表（627条）
│   ├── Skill_export.csv        # 技能定义表（181条）
│   ├── drop_table_weights.csv  # 掉落表权重
│   ├── map_quality_avg_out.csv # 地图品质概率与条件均价
│   └── skill_parsing_report.csv# 技能解析报告（含JSON字段映射）
├── logs/						
│   └── json_log_*.txt          # 提取的JSON日志文件
└── bidking-client/             ← 新建 Tauri v2 项目
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts
    ├── index.html
    ├── app-icon.png
    ├── src/
    │   ├── main.tsx
    │   ├── App.tsx             ← 核心前端：品质标签+估算
    │   ├── App.css             ← 暗色主题
    │   └── vite-env.d.ts
    └── src-tauri/
        ├── Cargo.toml
        ├── build.rs
        ├── tauri.conf.json
        ├── capabilities/default.json
        ├── icons/
        └── src/
            ├── main.rs
            ├── lib.rs
            └── pipe_listener.rs ← Named Pipe 服务端
```

## 3. 数据采集层 - monitor_ram.py

### 3.1 整体架构

```
网络监听(scapy) → 房间号提取 → 内存扫描(pymem) → JSON提取 → 文件输出
      ↓                ↓              ↓               ↓            ↓
  packet_callback  ROOM_ID_PATTERN  scan_utf16le_string  extract_json_near_event  save_json_log
```

### 3.2 运行流程

1. **附加进程**：通过 pymem 附加到 BidKing.exe
2. **网络监听**：scapy 监听 `8.133.195.27:10000` 的TCP流量
3. **房间号提取**：从网络包中匹配 `XX:XXXXXXXXXXXXXXXX` 格式的房间号
4. **两阶段扫描**：
   - 阶段1：仅扫描 `S2C_33_game_start_notify`，确认游戏开始并提取 ServerTime
   - 阶段2：扫描其余3类事件（next_round / use_item / game_over）
5. **JSON提取**：从事件字符串位置向后搜索UTF-16LE/UTF-8编码的JSON
6. **校验与保存**：验证 Uid 匹配、时间戳校验、去重、美化输出

### 3.3 关键函数说明

| 函数 | 职责 | 返回值 |
|------|------|--------|
| `scanning_worker` | 扫描主循环，管理两阶段扫描状态 | 无 |
| `_scan_by_event` | 单事件扫描：内存搜索→JSON提取→校验→保存 | `Tuple[bool, Optional[dict]]` |
| `extract_json_near_event` | 从事件地址提取JSON，单次内存读取+双编码尝试 | `Optional[Tuple[str,int,int,dict]]` |
| `_extract_utf16le_json_from_data` | 从字节数据提取UTF-16LE JSON（单次遍历+fallback） | 同上 |
| `_extract_utf8_json_from_data` | 从字节数据提取UTF-8 JSON（回退方案） | 同上 |
| `_try_extract_utf16le_json_from_pos` | 从指定位置尝试提取UTF-16LE JSON（含字符串字面量处理） | 同上 |
| `scan_utf16le_string` | 全内存搜索UTF-16LE字符串，支持 `first_only` 快速模式 | `List[int]` |
| `_validate_uid` | 校验 `parsed["GameData"]["Uid"] == room_id` | `bool` |
| `_validate_cast_time` | 校验 `CastTime(ms) > ServerTime(s)*1000` | `bool` |
| `save_json_log` | 去重+美化JSON+写入文件 | 无 |

### 3.4 事件类型与校验规则

| 事件 | 模式串 | Uid校验 | 时间校验 | 特殊处理 |
|------|--------|---------|----------|----------|
| 游戏开始 | `S2C_33_game_start_notify` | ✅ | - | 阶段1独占扫描；提取 ServerTime |
| 下一回合 | `S2C_37_game_next_round_notify` | ✅ | - | - |
| 使用道具 | `S2C_39_game_use_item` | - | ✅ CastTime>ServerTime | 不做Uid校验 |
| 游戏结束 | `S2C_45_game_over_notify` | ✅ | - | search_length×4；保存后终止扫描 |

### 3.5 性能优化要点

| 优化 | 方法 | 预估提速 |
|------|------|----------|
| 快速扫描 | `first_only=True`，pymem找到首个匹配即停 | 1-2s |
| 单次内存读取 | `extract_json_near_event` 只读一次，UTF-16LE/UTF-8共享data | 50-100ms |
| C级字符串搜索 | `bytes.find()` 替代 Python 循环搜索花括号 | 10-20ms |
| 早退机制 | `stop_on_first=True`，game_start找到即停 | 100-500ms |
| 单次JSON解析 | `parsed` dict 沿调用链传递，全程零重复 `json.loads` | 20-50ms |

### 3.6 CLI 参数

```bash
python monitor_ram.py [选项]
  -d, --duration    扫描持续时间（秒），默认300
  -i, --interval    扫描间隔（秒），默认3
  -l, --length      JSON搜索长度（字节），默认10240
  -o, --output      输出目录，默认logs
  --server-ip       服务器IP，默认8.133.195.27
  --server-port     服务器端口，默认10000
```

### 3.7 依赖

```
pymem>=1.13
scapy>=2.5
```

## 4. 游戏数据定义 - DataDefinitions

### 4.1 item_prices.csv — 物品价格表

627条物品记录，字段说明：

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `item_id` | int | 物品唯一ID | 1011001 |
| `name` | str | 物品名称 | 数据线 |
| `category_tags` | str(JSON list) | 类别标签列表 | `[101,107]` |
| `shape` | int | 占位形状编码 | 11 |
| `quality` | int | 品质等级 1-6 | 1 |
| `base_value` | int | 基础价值 | 160 |
| `grid_size` | str(JSON list) | 占位格数 [宽,高] | `[10,5]` |

**品质等级映射**：

| quality | 名称 | 颜色 |
|---------|------|------|
| 1 | 白色 | 普通 |
| 2 | 绿色 | 优秀 |
| 3 | 蓝色 | 精良 |
| 4 | 紫色 | 史诗 |
| 5 | 金色 | 传说 |
| 6 | 红色 | 神话 |

**类别标签映射**：

| tag | 类别 |
|-----|------|
| 100 | 特殊 |
| 101 | 家具物品 |
| 102 | 医疗药品 |
| 103 | 时尚潮流 |
| 104 | 兵装军火 |
| 105 | 珠宝矿藏 |
| 106 | 文物古董 |
| 107 | 数码娱乐 |
| 108 | 能源交通 |
| 109 | 食饮珍馐 |
| 110 | 书画古籍 |

**shape编码规则**：两位数字，十位=宽，个位=高。如 `11`=1×1格，`22`=2×2格，`31`=3×1格。实际占格数 = 宽×高。

### 4.2 Skill_export.csv — 技能定义表

181条技能记录，核心字段：

| 字段 | 说明 |
|------|------|
| `skill_id` | 技能ID |
| `name_zh` | 技能中文名 |
| `desc_zh` | 技能描述（含 `{0}` 占位符） |
| `param_07` | 技能类型：0=英雄技能, 1=竞拍信息, 2=道具技能 |
| `param_09` | 筛选参数：品质列表或类别标签 |
| `param_16` | 技能效果编码（如 `[1000]`=显示轮廓, `[7000]`=显示品质, `[6000]`=显示完整信息） |

**技能分类体系**：

| ID范围 | 类别 | 说明 |
|--------|------|------|
| 100-106 | 轮廓透视 | 显示物品轮廓（HitBoxList） |
| 200-205 | 总格数扫描 | 按品质统计总占格数（TotalHitBoxIndex） |
| 300-305 | 均格评估 | 按品质统计平均占格数（AllHitItemAvgBoxIndex） |
| 400-405 | 库存清点 | 按品质统计物品数量（HitItemIndex） |
| 500-505 | 估价审计 | 按品质统计总价值（HitItemTotalPrice） |
| 600-606 | 随机抽检 | 随机显示N件物品完整信息 |
| 700-706 | 品质鉴定 | 显示物品品质 |
| 801 | 类别检定 | 显示特定类别物品的轮廓+品质 |
| 2001-2010 | 类别鉴影 | 显示特定类别物品的轮廓 |
| 10000-10018 | 特殊检索 | 至宝/巨物/均价类技能 |
| 100xxx | 英雄技能 | 各角色专属技能 |
| 200xxx | 竞拍信息 | 回合开始时自动触发的信息 |

### 4.3 skill_parsing_report.csv — 技能解析报告

在 Skill_export 基础上增加了关键字段：

| 字段 | 说明 | 示例 |
|------|------|------|
| `bindings_skill_cid` | 技能结果绑定的JSON字段 | `total_grid_count<=TotalHitBoxIndex(int)` |
| `bindings_item_cid` | 物品结果绑定的JSON字段 | `total_grid_count<=TotalHitBoxIndex[主线]` |
| `skill_log_price_keys` | 日志中的价格键名 | `q4_price_avg<=AllHitItemAvgPrice` |
| `random_avg_role` | 随机均价推理说明 | `random_avg_price_min 推理: AllHitItemAvgPrice×HitItemIndex` |
| `outline_hitbox_role` | 轮廓HitBox推理说明 | `轮廓 HitBoxList → q5_count / q5_grid_count / q5_grid_avg` |

**JSON字段名映射**（从 bindings_skill_cid 提取）：

| 统计量 | JSON字段名 | 类型 |
|--------|-----------|------|
| 总格数 | `TotalHitBoxIndex` | int |
| 平均格数 | `AllHitItemAvgBoxIndex` | float |
| 物品数量 | `HitItemIndex` | int |
| 总价值 | `HitItemTotalPrice` | int |
| 平均价值 | `AllHitItemAvgPrice` | float |

**品质前缀**：`q1`(白), `q2`(绿), `q3`(蓝), `q4`(紫), `q5`(金), `q6`(红), `q12`(白+绿), `total`(全品质)

### 4.4 drop_table_weights.csv — 掉落表权重

定义物品掉落的权重分配：

| 字段 | 说明 |
|------|------|
| `drop_id` | 掉落表ID（如801） |
| `ref_id` | 引用的子表ID |
| `weight` | 权重 |
| `ref_type` | 引用类型（8=子掉落表） |

### 4.5 map_quality_avg_out.csv — 地图品质概率与条件均价

核心蒙特卡洛模拟数据源，按地图(tier)和品质组合提供：

| 字段 | 说明 | 示例 |
|------|------|------|
| `map_id` | 地图ID | 2101 |
| `tier` | 层级/类别 | 101 |
| `nest_drop_id` | 嵌套掉落表ID | 2001 |
| `quality_group` | 品质组合 | `q1`, `q1+q2`, `q1+q2+q3` 等 |
| `prob_in_group` | 该品质组合出现的概率 | 0.29365701 |
| `avg_price_per_item` | 该组合下每物品平均价值 | 202.7718 |
| `avg_price_per_cell` | 该组合下每格平均价值 | 118.4634 |

**品质概率分布**（以map_id=2101, tier=101为例）：

| 品质 | 概率 |
|------|------|
| q1(白) | 29.37% |
| q2(绿) | 31.32% |
| q3(蓝) | 29.37% |
| q4(紫) | 7.83% |
| q5(金) | 1.96% |
| q6(红) | 0.16% |

## 5. JSON日志格式（基于实际日志样例）

> 以下格式均来自 `logs/` 目录下的真实日志文件，房间号 `4401:1178745667499618`。

### 5.1 S2C_33_game_start_notify — 游戏开始

```json
{
  "GameData": {
    "Uid": "4401:1178745667499618",
    "MapId": 4401,
    "UserLog": [
      {
        "UserUid": "963996820112033",
        "Name": "37三七",
        "HeroCid": 204,
        "HeadCid": 121701,
        "SelectItemList": [
          { "ItemCid": 100110 },
          { "ItemCid": 100117 },
          { "ItemCid": 100129 },
          { "ItemCid": 100135 },
          { "ItemCid": 100104 }
        ]
      }
    ],
    "HeroSkillLog": [
      {
        "SkillCid": 100204,
        "HeroCid": 204,
        "CastTime": "1779712955147",
        "HitItemIndex": 44,
        "Uid": "1178745667500531"
      }
    ],
    "MapSkillLog": [
      {
        "SkillCid": 200031,
        "MapCid": 4401,
        "CastTime": "1779712955133",
        "AllHitItemAvgPrice": 2310,
        "Uid": "1178745667500405"
      }
    ],
    "NextRoundTime": "1779713035",
    "GameType": 1,
    "ServerTime": "1779712955"
  }
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `GameData.Uid` | string | 房间号，格式 `MapId:Uid` |
| `GameData.MapId` | int | 地图ID（如4401） |
| `GameData.ServerTime` | string | Unix时间戳（秒），字符串格式 |
| `GameData.GameType` | int | 游戏类型 |
| `GameData.NextRoundTime` | string | 下一回合时间（Unix秒） |
| `GameData.UserLog[]` | array | 所有玩家信息 |
| `GameData.HeroSkillLog[]` | array | 英雄技能日志（游戏开始时触发） |
| `GameData.MapSkillLog[]` | array | 地图技能日志（游戏开始时触发） |

**UserLog 子字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `UserUid` | string | 玩家UID |
| `Name` | string | 玩家昵称（可能缺失） |
| `HeroCid` | int | 英雄CID（如204=英雄204） |
| `HeadCid` | int | 头像CID |
| `HeroSkinCid` | int | 英雄皮肤CID（可选） |
| `HeadBoxCid` | int | 头像框CID（可选） |
| `TitleCid` | int | 称号CID（可选） |
| `SelectItemList[]` | array | 玩家携带的道具列表 |
| `SelectItemList[].ItemCid` | int | 道具CID |

### 5.2 S2C_37_game_next_round_notify — 下一回合

```json
{
  "GameData": {
    "Uid": "4401:1178745667499618",
    "MapId": 4401,
    "Round": 1,
    "UserLog": [
      {
        "UserUid": "432039350520126",
        "HeroCid": 107,
        "UseItemLog": [
          { "ItemCidOrPrice": 100136 }
        ],
        "PriceLog": [
          { "ItemCidOrPrice": 1 }
        ],
        "HeadCid": 120000,
        "HeroSkinCid": 1410702,
        "SelectItemList": [
          { "ItemCid": 100136, "IsUsed": true },
          { "ItemCid": 100101 },
          { "ItemCid": 100102 },
          { "ItemCid": 100129 },
          { "ItemCid": 100135 }
        ],
        "HeadBoxCid": 140702,
        "TitleCid": 150024
      }
    ],
    "HeroSkillLog": [
      {
        "SkillCid": 100204,
        "HeroCid": 204,
        "CastTime": "1779712955147",
        "HitItemIndex": 44,
        "Uid": "1178745667500531"
      },
      {
        "SkillCid": 1002041,
        "HeroCid": 204,
        "CastTime": "1779712990168",
        "CastRound": 1,
        "AllHitItemAvgBoxIndex": 3.42857146,
        "Uid": "1173385128139374"
      }
    ],
    "MapSkillLog": [
      {
        "SkillCid": 200031,
        "MapCid": 4401,
        "CastTime": "1779712955133",
        "AllHitItemAvgPrice": 2310,
        "Uid": "1178745667500405"
      }
    ],
    "ItemSkillLog": [
      {
        "SkillCid": 201,
        "ItemCid": 100104,
        "CastTime": "1779712976544",
        "Uid": "1173385128135285",
        "TotalHitBoxIndex": 22
      }
    ],
    "NextRoundTime": "1779713050",
    "GameType": 1,
    "ServerTime": "1779712990"
  }
}
```

**与 game_start 的关键差异**：

| 差异点 | game_start | game_next_round |
|--------|-----------|-----------------|
| `Round` | 无 | 有，当前回合号（从1开始） |
| `UserLog[].UseItemLog` | 无 | 有，本回合使用的道具记录 |
| `UserLog[].PriceLog` | 无 | 有，本回合出价记录 |
| `UserLog[].SelectItemList[].IsUsed` | 无 | 有，标记道具是否已使用 |
| `ItemSkillLog[]` | 无 | 有，本回合道具技能日志 |
| `HeroSkillLog[]` | 仅初始触发 | 累积所有回合的英雄技能日志 |

**UseItemLog / PriceLog 子字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ItemCidOrPrice` | int | UseItemLog中为道具CID；PriceLog中为出价排名（1/2/3/4） |
| `Round` | int | 可选，该记录所属回合号（缺失=当前回合） |

### 5.3 S2C_39_game_use_item — 使用道具（技能）

**格式1：简单统计型**（SkillCid=201, 301, 402 等）

```json
{
  "ItemSkillLog": [
    {
      "SkillCid": 201,
      "ItemCid": 100104,
      "CastTime": "1779712976544",
      "Uid": "1173385128135285",
      "TotalHitBoxIndex": 22
    }
  ]
}
```

```json
{
  "ItemSkillLog": [
    {
      "SkillCid": 301,
      "ItemCid": 100110,
      "CastTime": "1779713000348",
      "CastRound": 1,
      "AllHitItemAvgBoxIndex": 2.75,
      "Uid": "1173385128142334"
    }
  ]
}
```

```json
{
  "ItemSkillLog": [
    {
      "SkillCid": 402,
      "ItemCid": 100117,
      "CastTime": "1779713036133",
      "CastRound": 2,
      "HitItemIndex": 16,
      "Uid": "1173385128152312"
    }
  ]
}
```

**格式2：详细物品型**（SkillCid=602 随机抽检，含HitBoxList）

```json
{
  "ItemSkillLog": [
    {
      "SkillCid": 602,
      "ItemCid": 100129,
      "CastTime": "1779712449955",
      "CastRound": 3,
      "HitItemIndex": 2,
      "HitBoxList": [
        {
          "BoxId": 59,
          "ItemUid": "1178745666142708",
          "ItemCid": 1063002,
          "ItemSlotType": 12,
          "ItemType": [106, 110],
          "ItemQuility": 3,
          "ItemPrice": 1985,
          "ItemBoxIndex": 2
        },
        {
          "BoxId": 45,
          "ItemUid": "1178745666142694",
          "ItemCid": 1034005,
          "ItemSlotType": 11,
          "ItemType": [103, 101],
          "ItemQuility": 4,
          "ItemPrice": 2560,
          "ItemBoxIndex": 1
        }
      ],
      "AllHitItemAvgPrice": 2272.5,
      "AllHitBoxAvgPrice": 1515,
      "AllHitItemAvgBoxIndex": 1.5,
      "HitItemTotalPrice": 4545,
      "Uid": "1173247689126262",
      "TotalHitBoxIndex": 3
    }
  ]
}
```

**ItemSkillLog 通用字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `SkillCid` | int | 技能CID（如201=总格数, 301=均格, 402=库存, 602=随机抽检） |
| `ItemCid` | int | 使用的道具CID |
| `CastTime` | string | 施法时间（Unix毫秒），字符串格式 |
| `CastRound` | int | 可选，施法回合号 |
| `Uid` | string | 本次技能日志唯一ID |

**ItemSkillLog 统计结果字段**（按技能类型出现）：

| 字段 | 类型 | 说明 | 出现的SkillCid示例 |
|------|------|------|-------------------|
| `TotalHitBoxIndex` | int | 总格数 | 201, 602 |
| `AllHitItemAvgBoxIndex` | float | 平均格数 | 301 |
| `HitItemIndex` | int | 物品数量 | 402, 602 |
| `HitItemTotalPrice` | int | 总价值 | 602 |
| `AllHitItemAvgPrice` | float | 平均物品价值 | 602 |
| `AllHitBoxAvgPrice` | float | 平均每格价值 | 602 |

**HitBoxList 子字段**（随机抽检/品质鉴定/轮廓透视技能）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `BoxId` | int | 格子ID |
| `ItemUid` | string | 物品唯一ID |
| `ItemCid` | int | 可选，物品CID（仅完整信息型） |
| `ItemSlotType` | int | 可选，占位形状编码（11=1×1, 12=1×2, 22=2×2, 23=2×3, 13=1×3, 31=3×1） |
| `ItemType` | int[] | 可选，物品类别标签（仅完整信息型） |
| `ItemQuility` | int | 可选，品质等级1-6（品质鉴定/完整信息型） |
| `ItemPrice` | int | 可选，物品价格（仅完整信息型） |
| `ItemBoxIndex` | int | 可选，物品占格数（仅完整信息型） |

**ItemSlotType 编码规则**：两位数字，十位=宽，个位=高。`11`=1×1, `12`=1×2, `22`=2×2, `23`=2×3, `13`=1×3, `31`=3×1。实际占格数=宽×高。

### 5.4 S2C_45_game_over_notify — 游戏结束

游戏结束日志是最大的日志（可达80KB+），包含完整的仓库布局、所有玩家操作记录和技能日志。

```json
{
  "WinUserUid": "963996820112033",
  "GameData": {
    "Uid": "4401:1178745667499618",
    "MapId": 4401,
    "Round": 2,
    "StockContainer": {
      "StockId": -1,
      "StockCid": -1,
      "StockBoxes": [
        {
          "BoxId": 165,
          "Position": { "X": 5, "Y": 16 },
          "Item": {}
        },
        {
          "BoxId": 32,
          "Position": { "X": 2, "Y": 3 },
          "Item": {
            "Uid": "1178745667500375",
            "Cid": 1054005,
            "Count": 1,
            "BoxPositionData": [
              { "X": 2, "Y": 3 }
            ],
            "CanTrade": true
          }
        }
      ]
    },
    "UserLog": [ ... ],
    "HeroSkillLog": [ ... ],
    "MapSkillLog": [ ... ],
    "ItemSkillLog": [ ... ],
    "GameType": 1,
    "ServerTime": "1779713060"
  },
  "OldCollectionExp": 2198319,
  "NewCollectionExp": 2201837,
  "LossRecovery": "17733",
  "UserSkillList": [ ... ]
}
```

**game_over 独有字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `WinUserUid` | string | 获胜玩家UID |
| `GameData.Round` | int | 最终回合号 |
| `GameData.StockContainer` | object | 仓库完整布局（**随机生成**，不同入场费的地图格子范围不同） |
| `OldCollectionExp` | int | 旧收藏经验值 |
| `NewCollectionExp` | int | 新收藏经验值 |
| `LossRecovery` | string | 亏损补偿 |
| `UserSkillList[]` | array | 每个玩家的完整技能视角（含HitBoxList） |

**StockContainer.StockBoxes[] 子字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `BoxId` | int | 格子ID |
| `Position` | object | 格子在仓库中的坐标 `{X, Y}` |
| `Item` | object | 空对象`{}`=空格；有内容=物品信息 |
| `Item.Uid` | string | 物品唯一ID |
| `Item.Cid` | int | 物品CID（可关联item_prices.csv） |
| `Item.Count` | int | 数量 |
| `Item.BoxPositionData[]` | array | 物品占据的所有格子坐标 |
| `Item.CanTrade` | bool | 是否可交易（缺失=不可交易，如红色品质） |

**UserSkillList[] 子字段**（game_over独有，记录每个玩家的完整技能视角）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `UserUid` | string | 玩家UID |
| `HeroSkillLog[]` | array | 该玩家视角的英雄技能日志 |
| `MapSkillLog[]` | array | 该玩家视角的地图技能日志 |
| `ItemSkillLog[]` | array | 该玩家视角的道具技能日志 |

UserSkillList 中的技能日志结构与 GameData 中的相同，但包含该玩家使用的所有技能的完整 HitBoxList 信息。

### 5.5 三类技能日志的对比

游戏中存在三类技能，它们的日志结构相同但触发方式和归属不同：

| 技能类型 | 日志位置 | 触发时机 | 频率 | SkillCid格式 |
|---------|---------|---------|------|-------------|
| 英雄技能 | `HeroSkillLog[]` | 每轮开始时自动触发 | 每轮1次 | `100{HeroCid}{Round}` 如100204, 1002041, 1002042 |
| 地图技能 | `MapSkillLog[]` | 每轮开始时自动触发 | 每轮1次 | `200xxx` 如200031, 200010 |
| 道具技能 | `ItemSkillLog[]` | 玩家主动使用道具 | 每轮最多1次，需实时监听 `game_use_item` 信号 | `201-801`, `10000-10018` 等 |

**英雄技能**：每轮开始时自动触发一次，不出现在 `game_use_item` 事件中，仅在 `game_start` 和 `game_next_round` 的 `HeroSkillLog` 中记录。

**地图技能**：每轮开始时自动触发一次，不出现在 `game_use_item` 事件中，仅在 `game_start` 和 `game_next_round` 的 `MapSkillLog` 中记录。

**道具技能**：玩家主动使用，每轮最多使用一次。客户端需要实时监听 `game_use_item` 信号，从信号中解析 `ItemSkillLog` 获取技能结果。道具技能也会累积出现在后续 `game_next_round` 和 `game_over` 的 `ItemSkillLog` 中。

**英雄技能SkillCid编码**：`100` + `HeroCid`(3位) + `Round`(1位)。如 `100204`=英雄204初始技能, `1002041`=英雄204第1回合技能, `1002042`=英雄204第2回合技能。

**地图技能SkillCid编码**：`200` + 序号。如 `200031`=地图均价信息, `200010`=地图总格数信息。

### 5.6 日志累积规则

| 事件 | HeroSkillLog | MapSkillLog | ItemSkillLog |
|------|-------------|-------------|--------------|
| game_start | 仅初始触发 | 仅初始触发 | 无 |
| game_next_round | 累积所有回合 | 累积所有回合 | 累积所有回合 |
| game_use_item | 无 | 无 | 仅本次技能 |
| game_over | 累积所有回合 | 累积所有回合 | 累积所有回合 |

关键点：`game_next_round` 和 `game_over` 中的 HeroSkillLog/MapSkillLog/ItemSkillLog 是**累积**的，包含之前所有回合的记录。`game_use_item` 中只有本次使用的单个技能记录。

## 6. Tauri 客户端技术方案（规划中）

### 6.1 技术选型

| 层 | 技术 | 说明 |
|----|------|------|
| 框架 | Tauri v2 | Rust后端 + Web前端，轻量桌面应用 |
| 前端 | React + TypeScript | UI渲染 |
| 后端 | Rust | 数据处理、IPC通信 |
| 进程通信 | Named Pipe (命名管道) | Python脚本 → Tauri客户端，轻量高效 |
| 数据存储 | SQLite (via tauri-plugin-sql) | 历史对局记录 |

### 6.2 进程通信方案：Named Pipe

Python 脚本与 Tauri 客户端同机运行，使用 Windows Named Pipe 进行进程间通信（IPC），比 WebSocket 更轻量，无需额外端口。

**通信协议**：

```
Python (monitor_ram.py)                    Tauri (Rust)
       │                                        │
       │  连接命名管道: \\.\pipe\bidking_log     │
       │───────────────────────────────────────→│
       │                                        │
       │  发送事件消息 (JSON per line)           │
       │  {"type":"game_start","data":{...}}     │
       │───────────────────────────────────────→│
       │  {"type":"game_use_item","data":{...}}  │
       │───────────────────────────────────────→│
       │  {"type":"game_next_round","data":{...}}│
       │───────────────────────────────────────→│
       │  {"type":"game_over","data":{...}}      │
       │───────────────────────────────────────→│
       │                                        │
```

**消息格式**：每行一个JSON，以 `\n` 分隔。

```json
{
  "type": "game_start | game_use_item | game_next_round | game_over",
  "room_id": "4401:1178745667499618",
  "timestamp": "2026-05-25T20:42:37",
  "data": { ... }
}
```

**Python端实现**（需修改 monitor_ram.py）：

```python
import win32pipe, win32file

PIPE_NAME = r"\\.\pipe\bidking_log"

def send_to_client(event_type, room_id, data):
    msg = json.dumps({
        "type": event_type,
        "room_id": room_id,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }) + "\n"
    win32file.WriteFile(pipe_handle, msg.encode("utf-8"))
```

**Rust端实现**（Tauri后端）：

```rust
use tokio::net::windows::named_pipe::ServerOptions;
use tauri::AppHandle;

const PIPE_NAME: &str = r"\\.\pipe\bidking_log";

async fn listen_pipe(app: AppHandle) {
    let server = ServerOptions::new()
        .first_pipe_instance(true)
        .create(PIPE_NAME)
        .unwrap();
    loop {
        server.connect().await.unwrap();
        let mut buf = [0u8; 65536];
        loop {
            let len = server.read(&mut buf).await.unwrap();
            let msg: Value = serde_json::from_slice(&buf[..len]).unwrap();
            app.emit("game-event", &msg).unwrap();
        }
    }
}
```

**备选方案对比**：

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **Named Pipe** ✅ | 无端口占用、低延迟、原生Windows支持 | 仅限本机 | 同机进程通信（推荐） |
| stdin/stdout | 最简单 | 需Python作为Tauri子进程 | 简单集成 |
| 文件监听 | 无需修改Python | 延迟高、轮询开销 | 低实时性场景 |
| WebSocket | 跨网络 | 端口占用、重量级 | 跨机器通信（过重） |

### 6.3 模块划分

```
bidking-client/
├── src-tauri/
│   ├── src/
│   │   ├── main.rs
│   │   ├── ipc_listener.rs    # Named Pipe 监听，接收Python推送的事件
│   │   ├── data_loader.rs     # CSV数据加载（item_prices, skills）
│   │   ├── json_parser.rs     # JSON日志解析，提取关键key-value
│   │   ├── stats.rs           # 统计计算（品质分布、格数、价值）
│   │   └── commands.rs        # Tauri命令（前端调用接口）
│   └── Cargo.toml
├── src/                        # React前端
│   ├── App.tsx
│   ├── components/
│   │   ├── GameDashboard.tsx  # 对局仪表盘
│   │   ├── SkillPanel.tsx     # 技能信息面板
│   │   └── ItemTable.tsx      # 物品清单表
│   └── hooks/
│       └── useGameLog.ts      # 游戏日志数据Hook
└── package.json
```

### 6.4 数据流

```
Python脚本(monitor_ram.py)
    │
    │  提取JSON后，同时：
    │  1. 保存到 logs/ 目录（文件备份）
    │  2. 通过 Named Pipe 推送到 Tauri 客户端（实时展示）
    ▼
Tauri Rust后端
    │
    ├── ipc_listener: 监听 Named Pipe，接收事件消息
    ├── json_parser: 解析JSON，提取 SkillCid + 统计字段
    ├── data_loader: 加载CSV，建立 item_id→价格/格数/品质 映射
    └── stats: 根据技能结果计算已知信息
              如: SkillCid=201 → TotalHitBoxIndex=22 (总格数22)
              如: SkillCid=301 → AllHitItemAvgBoxIndex=2.75 (均格2.75)
    ▼
Tauri前端 (via event emit)
    └── 实时展示：技能结果 + 物品统计 + 对局信息
```

### 6.5 JSON关键信息提取逻辑

客户端收到事件消息后，按事件类型分别处理：

**game_start**：
1. 提取 `MapId` → 确定地图类型
2. 提取 `HeroSkillLog` → 获取英雄初始技能结果
3. 提取 `MapSkillLog` → 获取地图初始技能结果（如 `AllHitItemAvgPrice`）
4. 提取 `UserLog` → 获取所有玩家信息、英雄、道具列表

**game_use_item**（实时监听，每轮最多1次）：
1. 提取 `ItemSkillLog[0].SkillCid` → 查 `skill_parsing_report.csv` 获取技能含义
2. 提取统计结果字段（`TotalHitBoxIndex` / `AllHitItemAvgBoxIndex` / `HitItemIndex` 等）
3. 若含 `HitBoxList` → 提取物品详细信息（CID/品质/价格/格数）

**game_next_round**：
1. 提取 `Round` → 更新当前回合号
2. 提取累积的 `HeroSkillLog` / `MapSkillLog` → 获取本回合新增的英雄/地图技能结果
3. 提取累积的 `ItemSkillLog` → 获取本回合之前使用的道具技能结果
4. 提取 `UserLog[].UseItemLog` / `PriceLog` → 获取本回合玩家操作

**game_over**：
1. 提取 `WinUserUid` → 获胜玩家
2. 提取 `StockContainer` → 完整仓库布局（随机生成，不同入场费地图格子范围不同）
3. 提取 `UserSkillList` → 每个玩家的完整技能视角

**技能结果示例**：

| SkillCid | 技能名 | 统计字段 | 示例值 | 含义 |
|----------|--------|---------|--------|------|
| 201 | 总仓储空间 | `TotalHitBoxIndex` | 22 | 所有物品总占22格 |
| 301 | 均格评估 | `AllHitItemAvgBoxIndex` | 2.75 | 平均每件物品占2.75格 |
| 402 | 库存清点 | `HitItemIndex` | 16 | 共16件物品 |
| 602 | 随机抽检 | `HitBoxList` + 统计 | (含2件物品详情) | 随机2件物品完整信息 |
| 1002041 | 英雄204第1轮 | `AllHitItemAvgBoxIndex` | 3.43 | 英雄技能：均格3.43 |
| 200031 | 地图均价 | `AllHitItemAvgPrice` | 2310 | 地图技能：均价2310 |

### 6.6 客户端需读取的DataDefinitions文件

| 文件 | 读取方式 | 用途 |
|------|----------|------|
| `item_prices.csv` | 启动时全量加载 | 物品ID→名称/价值/格数/品质映射 |
| `Skill_export.csv` | 启动时全量加载 | SkillId→技能名/描述/参数映射 |
| `skill_parsing_report.csv` | 启动时全量加载 | SkillId→JSON字段名映射（解析技能结果） |
| `map_quality_avg_out.csv` | 启动时全量加载 | MapId→品质概率分布+条件均价（参考数据） |
| `drop_table_weights.csv` | 暂不加载 | 语义待确认 |

## 7. 依赖关系图

```
monitor_ram.py
  ├── pymem        → 进程附加、内存读取、模式扫描
  ├── pymem.pattern → UTF-16LE字符串搜索
  ├── scapy        → 网络包监听、TCP过滤
  └── pywin32      → Named Pipe 客户端（向Tauri推送事件）

Tauri客户端（规划中）
  ├── tauri v2           → 桌面应用框架
  ├── tauri-plugin-sql   → SQLite存储
  ├── tokio              → 异步运行时 + Named Pipe 服务端
  ├── serde + csv        → Rust CSV解析
  ├── react              → 前端UI
  ├── recharts / echarts → 前端图表
  └── tailwindcss        → 样式
```

## 8. 运行方式

### 8.1 数据采集

```bash
# 安装依赖
pip install -r requirements.txt

# 以管理员权限运行（pymem需要读取其他进程内存）
python monitor_ram.py

# 自定义参数
python monitor_ram.py -d 600 -i 5 -l 16384 -o my_logs
```

**前置条件**：
- Windows 操作系统
- BidKing.exe 正在运行
- 管理员权限（读取进程内存）
- Npcap 已安装（scapy抓包依赖）

### 8.2 Tauri客户端（规划中）

```bash
# 开发
cd bidking-client
npm install
npm run tauri dev

# 构建
npm run tauri build
```

## 9. 信息不足与待确认项

### 9.1 已确认可实现的模块

| 模块 | 状态 | 依据 |
|------|------|------|
| JSON日志解析 | ✅ 可实现 | 日志格式已明确，skill_parsing_report提供了字段映射 |
| 技能结果提取 | ✅ 可实现 | SkillCid→统计字段映射链完整 |
| 物品统计展示 | ✅ 可实现 | item_prices.csv 提供完整的品质/格数/价值数据 |
| Named Pipe IPC通信 | ✅ 可实现 | Windows原生支持，Python端pywin32，Rust端tokio |

### 9.2 暂不实现的模块

| 模块 | 原因 |
|------|------|
| 蒙特卡洛模拟 | drop_table_weights 语义待确认，暂不编写 |

### 9.3 待确认/信息不足的项

| 项 | 问题 | 影响 |
|----|------|------|
| drop_table_weights的完整语义 | ref_type=8的子表如何嵌套？权重如何归一化？ | 蒙特卡洛模块的前置条件 |
| IsStandDown字段的含义 | 玩家827657377855701有此字段，含义不确定 | 玩家状态判断 |
| StockContainer仓库尺寸规则 | 不同入场费的地图格子范围不同，具体规则待确认 | 仓库布局展示 |
