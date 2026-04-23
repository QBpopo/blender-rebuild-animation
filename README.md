# Blender Animation Reverse Pipeline

一套 Blender 动画数据逆向工程工具链，可将多个 `.blend` 文件中的动画数据导出为 TOML，按权重策略合并并智能优化，最终重建回 `.blend` 文件。

## 系统架构

```txt
┌─────────────┐     ┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  old.py     │     │  merge.py    │     │  old_to_new.py│     │  engine.py   │
│  导出原始   │ ──► │  权重合并    │ ──► │  智能优化     │ ──► │  重建动画    │
│  动画数据   │     │  冲突解决    │     │  关键帧精简   │     │  写回 blend  │
└─────────────┘     └──────────────┘     └───────────────┘     └──────────────┘
     .blend              old.toml            new.toml              .blend
```

**完整流程由 `main.py` 编排，通过 `config.toml` 配置。**

## 文件说明

| 文件 | 运行环境 | 说明 |
|------|----------|------|
| `main.py` | Python | 管线入口，编排完整流程 |
| `old.py` | Blender | 从 `.blend` 导出原始动画数据到 `*_old.toml` |
| `merge.py` | Python | 合并多个 `old.toml`，支持权重策略与多种冲突解决方式 |
| `old_to_new.py` | Python | 将 `old.toml` 优化为 `new.toml`，包含多种关键帧精简算法 |
| `engine.py` | Blender | 从 `new.toml` 重建动画并写回 `.blend` |
| `config.toml` | — | 全局配置文件 |

### 内部模块（拆分后的职责模块）

| 文件 | 说明 |
|------|------|
| `toml_io.py` | TOML 序列化/反序列化 |
| `path_utils.py` | 路径解析、文件复制 |
| `anim_utils.py` | 动画数据辅助函数 |
| `subprocess_utils.py` | 子进程执行 |
| `blender_common.py` | Blender 脚本公共样板（参数解析、路径注入） |
| `utils.py` | 向后兼容的重导出门面 |

## 快速开始

### 前置要求

- Python 3.11+（需要 `tomllib` 内置模块）
- Blender（需在 `config.toml` 中指定可执行文件路径）

### 运行

```bash
python main.py --config config.toml
```

不指定 `--config` 时默认使用同目录下的 `config.toml`。

### 单步运行

```bash
# 仅导出（在 Blender 中运行）
blender --background model.blend --python old.py -- --output old.toml

# 仅合并（纯 Python）
python merge.py --inputs a_old.toml b_old.toml --output merged.toml --labels charA charB

# 仅优化（纯 Python）
python -c "from old_to_new import convert_file; convert_file('old.toml', 'new.toml')"

# 仅重建（在 Blender 中运行）
blender --background model.blend --python engine.py -- --input new.toml --output result.blend
```

## 配置参考

### `[blender]`

| 键 | 类型 | 必填 | 说明 |
|----|------|------|------|
| `exe` | string | 是 | Blender 可执行文件绝对路径 |

### `[[tasks]]` — 任务定义

每个 `[[tasks]]` 定义一个独立任务，执行完整管线。

| 键 | 类型 | 必填 | 说明 |
|----|------|------|------|
| `label` | string | 是 | 任务标签，用于生成输出目录 `{label}.tmp/` |
| `new_blend` | string | 否 | 模板 blend 文件路径，用于重建动画 |
| `cache` | bool | 否 | 是否保留每次运行输出，默认 `false`（覆盖写入） |

### `[[tasks.sources]]` — 源文件定义

每个 `[[tasks.sources]]` 定义一个源 blend 文件。

| 键 | 类型 | 必填 | 说明 |
|----|------|------|------|
| `path` | string | 是 | blend 文件路径 |
| `label` | string | 是 | 源标签，用于输出文件命名 |
| `weight_ranges` | array | 否 | 帧范围权重列表，不指定时默认权重 1.0 |

#### 输出目录结构

运行后会在 `new_blend` 同级目录下生成（未指定 `new_blend` 时为首个源文件同级目录）：

```
{tasks.label}.tmp/
  - {tasks.sources.label}.toml                    (原始导出)
  - {tasks.label}.toml                             (优化后，cache=false)
  - {tasks.label}.{datetime}.toml                  (优化后，cache=true)
  - {tasks.label}.report.toml                      (优化报告，cache=false)
  - {tasks.label}.{datetime}.report.toml           (优化报告，cache=true)
  - {tasks.label}.blend                            (重建动画，cache=false)
  - {tasks.label}.{datetime}.blend                 (重建动画，cache=true)
```

- **`cache = false`**（默认）：每次运行覆盖写入，不会产生 `.blend1` 备份文件
- **`cache = true`**：保留历史输出，文件名带时间后缀 `{datetime}`

例如 `label = "walk"` 的任务，源标签为 `charA`，会产生 `walk.tmp/charA.toml`（原始导出）和 `walk.tmp/walk.blend`（覆盖模式）或 `walk.tmp/walk.20260423_143025.blend`（缓存模式）等文件。

#### `weight_ranges` 每项

| 键 | 类型 | 必填 | 说明 |
|----|------|------|------|
| `frame_start` | int/float | 是 | 起始帧 |
| `frame_end` | int/float | 是 | 结束帧 |
| `weight` | float | 是 | 权重值（越高优先级越高，0 表示不参与竞争） |

#### 权重机制详解

权重决定当多个源文件存在**同名 Action** 或**同一对象绑定**时，哪个源的数据优先保留。

系统计算每个源在冲突范围内的**有效权重**：

```
有效权重 = Σ (weight × 与 Action/绑定帧范围的交集帧数)
```

- **Action 冲突**：有效权重最高的源（支配源）保留原始 Action 名称，其余源自动重命名（格式由 `action_rename_template` 控制）
- **对象绑定冲突**：总权重最高的源的绑定生效
- **weight = 0**：表示该源在该帧范围内完全"弃权"，不参与竞争

**示例**：

```toml
[[tasks]]
label = "merge_demo"
cache = true

[[tasks.sources]]
path = "../draft/a.blend"
label = "charA"

[[tasks.sources.weight_ranges]]
frame_start = 0
frame_end = 133
weight = 4

[[tasks.sources.weight_ranges]]
frame_start = 133
frame_end = 168
weight = 0

[[tasks.sources]]
path = "../draft/b.blend"
label = "charB"

[[tasks.sources.weight_ranges]]
frame_start = 0
frame_end = 133
weight = 0

[[tasks.sources.weight_ranges]]
frame_start = 133
frame_end = 168
weight = 4
```

结果：0-133 帧由 a.blend 主导，133-168 帧由 b.blend 主导。

### 旧配置格式（向后兼容）

```toml
[[tasks]]
label = "my_task"
old_blend = "../draft/a.blend"
extra_old_blends = ["../draft/b.blend"]
extra_source_labels = ["label_b"]
```

旧格式仍可使用，但 `label` 字段必填。旧格式无权重功能（默认权重 1.0）。新旧格式可在不同 task 中混合使用。

### `[merge]`

| 键 | 默认值 | 说明 |
|----|--------|------|
| `action_conflict_strategy` | `"rename"` | Action 名称冲突策略（见下表） |
| `action_rename_template` | `"{source}.{name}"` | 重命名模板（rename/weighted 策略时对非优先源生效） |
| `binding_conflict_strategy` | `"keep_last"` | 对象绑定冲突策略 |
| `scene_conflict_strategy` | `"union"` | 场景设置冲突策略 |

#### 策略选项

| 策略 | Action 冲突 | 绑定冲突 | 说明 |
|------|-------------|----------|------|
| `rename` | 重命名 | 重命名 | 冲突时按模板重命名 |
| `keep_first` | 保留首个 | 保留首个 | 忽略后续 |
| `keep_last` | 保留末个 | 保留末个 | 覆盖前面的 |
| `merge_fcurves` | 合并 fcurve | 重命名 | 不同 data_path 保留，相同 data_path 后者覆盖 |
| `weighted` | **权重优先** | **权重优先** | 权重最高者保留原始名称，其余重命名（推荐搭配 weight_ranges） |

### `[optimize]`

所有优化项均可通过 `enable_* = false` 单独关闭。

#### 优化算法执行顺序

对每个 fcurve 依次执行：

1. **重复关键帧去重** — 移除帧号和数值相同的连续关键帧
2. **BEZIER → LINEAR** — 检测贝塞尔曲线中接近线性的段，降级插值并移除手柄
3. **密集冗余帧去除** — 移除时间间隔短且数值变化小的关键帧
4. **次要关键帧剪枝** — LINEAR 插值中可由前后帧精确还原时移除
5. **Ramer-Douglas-Peucker 简化** — 经典曲线简化算法，减少关键帧同时保持形状
6. **线性模式检测** — 检测等差递增/递减序列，替换为 `linear_ramp` 过程式模式

#### 参数表

##### 重复关键帧去重

| 键 | 默认值 | 说明 |
|----|--------|------|
| `enable_duplicate_keyframe_prune` | `true` | 启用 |
| `duplicate_epsilon_frame` | `0.000001` | 帧号容差 |
| `duplicate_epsilon_value` | `0.000001` | 数值容差 |

##### BEZIER → LINEAR 简化

| 键 | 默认值 | 说明 |
|----|--------|------|
| `enable_bezier_to_linear` | `true` | 启用 |
| `bezier_to_linear_threshold` | `0.01` | 偏离线性阈值，越小越保守 |

##### 密集冗余关键帧去除

| 键 | 默认值 | 说明 |
|----|--------|------|
| `enable_interval_dedupe` | `true` | 启用 |
| `interval_dedupe_value_threshold` | `0.0001` | 数值变化阈值 |
| `dedupe_interval_sec` | `0.2` | 最小帧间隔（秒，会乘以 fps 转为帧） |

##### 次要关键帧剪枝

| 键 | 默认值 | 说明 |
|----|--------|------|
| `enable_minor_keyframe_prune` | `true` | 启用 |
| `minor_prune_allow_interpolations` | `["LINEAR"]` | 允许被剪枝的插值类型 |
| `minor_prune_neighbor_interpolations` | `["LINEAR", "CONSTANT"]` | 邻居帧允许的插值类型 |

##### Ramer-Douglas-Peucker 曲线简化

| 键 | 默认值 | 说明 |
|----|--------|------|
| `enable_rdp_simplify` | `true` | 启用 |
| `rdp_epsilon` | `0.001` | RDP 容差，越大简化越激进 |

##### 各类型 fcurve 剪枝阈值

| 键 | 默认值 | 说明 |
|----|--------|------|
| `rotation_threshold_deg` | `1.0` | 旋转阈值（度） |
| `location_threshold_px` | `0.5` | 位移阈值（像素） |
| `scale_threshold` | `0.01` | 缩放阈值 |
| `expression_threshold` | `0.001` | 表情/形变阈值 |
| `other_threshold` | `0.001` | 其他类型阈值 |
| `min_keyframes_per_fcurve` | `1` | 每 fcurve 最少保留帧数 |

##### 线性模式检测

| 键 | 默认值 | 说明 |
|----|--------|------|
| `enable_linear_pattern_detection` | `true` | 启用 |
| `linear_pattern_min_points` | `3` | 最少点数才触发检测 |
| `linear_pattern_frame_epsilon` | `0.00001` | 帧步长容差 |
| `linear_pattern_value_epsilon` | `0.00001` | 数值步长容差 |
| `keep_procedural_source_keyframes` | `false` | 检测到模式后是否保留原始帧 |

##### 输出格式精简

| 键 | 默认值 | 说明 |
|----|--------|------|
| `omit_default_interpolation` | `true` | 省略默认插值（BEZIER） |
| `omit_handles_for_linear_constant` | `true` | LINEAR/CONSTANT 帧省略手柄 |
| `omit_kind_field` | `true` | 省略 kind 字段 |
| `omit_default_extrapolation` | `true` | 省略默认外推（CONSTANT） |

##### 排除/过滤规则

| 键 | 默认值 | 说明 |
|----|--------|------|
| `prune_exclude_data_path_keywords` | `[]` | 含这些关键词的 data_path 跳过剪枝 |
| `pattern_exclude_data_path_keywords` | `[]` | 含这些关键词的 data_path 跳过模式检测 |
| `action_include_patterns` | `[]` | Action 名称 glob 白名单（空=全部） |
| `action_exclude_patterns` | `[]` | Action 名称 glob 黑名单 |
| `keep_empty_actions` | `false` | 保留优化后无 fcurve 的 action |

## TOML 数据格式

### old.toml（原始数据）

```toml
[meta]
format = "raw_animation_toml"
version = "1.0"

[[actions]]
name = "WalkAction"
frame_range = [1.0, 100.0]

[[actions.fcurves]]
data_path = "pose.bones[\"Head\"].rotation_euler"
array_index = 0
extrapolation = "CONSTANT"

[[actions.fcurves.keyframes]]
frame = 1.0
value = 0.0
interpolation = "BEZIER"
handle_left = [0.5, 0.0]
handle_right = [1.5, 0.1]
```

### new.toml（优化后数据）

```toml
[meta]
format = "optimized_animation_toml"
version = "1.0"

[[optimized_actions]]
name = "WalkAction"
frame_range = [1.0, 100.0]

[[optimized_actions.procedural_patterns]]
algorithm = "linear_ramp"
target_data_path = "pose.bones[\"Head\"].rotation_euler"
frame_start = 1.0
frame_step = 1.0
count = 100
value_start = 0.0
value_step = 0.01
```

### report.toml（优化报告）

```toml
[summary]
input_actions = 3
output_actions = 3
input_keyframes = 50000
output_keyframes = 3000
procedural_keyframes = 1500
deleted_keyframes = 45500
procedural_patterns = 12
compression_percent = 91.0
```
