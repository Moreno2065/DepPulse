# DepPulse

**DepPulse** 是一款本地源码依赖拓扑与变更影响审计 CLI 工具。它能扫描代码仓库，使用语言专用扫描器提取文件级和符号级依赖，构建有向依赖图，分析变更文件的上游影响范围，计算爆炸半径和风险评分，检测依赖循环，生成调用图，并输出人类可读、机器可读和 SARIF 格式的审计报告。

---

## 目录

- [安装](#安装)
- [快速上手](#快速上手)
- [命令参考](#命令参考)
- [图模型](#图模型)
- [调用图](#调用图)
- [爆炸半径](#爆炸半径)
- [风险评分](#风险评分)
- [扫描器限制](#扫描器限制)
- [配置](#配置)
- [缓存](#缓存)
- [CI 集成](#ci-集成)
- [输出格式](#输出格式)
- [开发](#开发)
- [路线图](#路线图)

---

## 安装

```bash
# 以开发模式安装
pip install -e .

# 安装开发依赖
pip install -e ".[dev]"
```

验证安装：

```bash
deppulse --help
# 或
python -m deppulse --help
```

---

## 快速上手

```bash
# 扫描项目
deppulse scan ./my-project

# 追踪单个文件变更的影响
deppulse trace ./my-project src/core/engine.py

# 分析 git diff 的影响
deppulse diff ./my-project

# 检测依赖循环
deppulse cycles ./my-project

# 生成完整审计报告
deppulse report ./my-project --markdown-output audit.md

# 构建符号级调用图
deppulse callgraph ./my-project

# 可视化依赖图
deppulse viz ./my-project --format html
```

---

## 命令参考

### `deppulse scan <路径>`

扫描项目并构建依赖图。

```bash
deppulse scan ./my-project
deppulse scan ./my-project --show-table
deppulse scan ./my-project --json
deppulse scan ./my-project --sarif-output report.sarif
deppulse scan ./my-project --incremental
deppulse scan ./my-project --since "1 week ago"
deppulse --no-cache scan ./my-project
```

### `deppulse trace <路径> <变更文件>`

追踪一个或多个文件的变更影响。显示受影响的上游依赖文件、爆炸半径和风险评分。

```bash
deppulse trace ./my-project src/utils/helpers.py
deppulse trace ./my-project src/core/model.py --show-chains
deppulse trace ./my-project a.py b.py c.py --json
```

### `deppulse diff <路径>`

分析 git diff 的变更影响。检测变更文件，过滤出依赖图中的文件，计算综合爆炸半径。

```bash
deppulse diff ./my-project          # 工作区 vs HEAD
deppulse diff ./my-project --staged  # 已暂存的变更
deppulse diff ./my-project --ref main  # 与 main 分支对比
deppulse diff ./my-project --markdown impact.md
```

### `deppulse cycles <路径>`

使用 Tarjan/Johnson 算法检测项目依赖图中的所有依赖循环。

```bash
deppulse cycles ./my-project
deppulse cycles ./my-project --json
```

### `deppulse report <路径>`

生成 JSON、Markdown 和/或 SARIF 格式的综合审计报告。

```bash
deppulse report ./my-project
deppulse report ./my-project --json-output report.json
deppulse report ./my-project --markdown-output report.md
deppulse report ./my-project --sarif-output report.sarif
deppulse report ./my-project --include-cycles --include-risk
```

### `deppulse callgraph <路径>`

从扫描结果构建符号级调用图，展示文件间的函数/方法调用关系。

```bash
deppulse callgraph ./my-project
deppulse callgraph ./my-project --format mermaid
deppulse callgraph ./my-project --format dot --output graph.dot
deppulse callgraph ./my-project --format json --output callgraph.json
deppulse callgraph ./my-project --file src/core/engine.py
deppulse callgraph ./my-project --max-nodes 50
```

### `deppulse viz <路径>`

以多种格式生成可视化依赖图。

```bash
deppulse viz ./my-project                           # 交互式 HTML 仪表盘
deppulse viz ./my-project --format html --output dashboard.html
deppulse viz ./my-project --format mermaid          # 文本流程图
deppulse viz ./my-project --format dot             # Graphviz DOT
deppulse viz ./my-project --focus src/core.py     # 聚焦特定文件
deppulse viz ./my-project --depth 2                # 仅显示 2 跳邻居
```

### `deppulse doctor <路径>`

验证环境与项目就绪状态。检查 git 仓库、支持的文件类型、配置、缓存状态和可用扫描器。

```bash
deppulse doctor ./my-project
```

---

## 图模型

DepPulse 构建一个 `networkx.DiGraph`，其中**有向边从源文件指向其依赖项**。

```
app.py 导入了 services/api.py
app.py 导入了 utils/helpers.py

    app.py
    /    \
services/api.py   utils/helpers.py
```

**影响分析**沿图的**反向（上游）**遍历，找到所有依赖变更文件的上游文件。

### 节点元数据

每个图节点存储：
- `path` — 相对于项目根目录的 POSIX 路径
- `language` — `python`、`java`、`kotlin` 或 `cpp`
- `suffix` — 文件扩展名
- `size_bytes` — 文件大小
- `symbol_count` — 提取的符号数量
- `unresolved_count` — 未解析的导入数量

### 边元数据

每条有向边存储：
- `raw_text` — 原始导入/包含文本
- `kind` — `import`、`java_import`、`kotlin_import`、`include_local` 或 `include_system`
- `line_number` — 依赖所在源代码行号
- `resolved_by` — 扫描器名称

---

## 调用图

符号级调用图（`deppulse callgraph`）在文件级依赖图的基础上，展示函数和方法之间的调用关系。

每个**节点**是一个符号：`(file_path, symbol_name, symbol_type)`
每条**边**表示调用关系：`(调用方符号) → (被调用方符号)`

**支持的语言：**
- **Python** — 使用 AST 调用提取（`ast.NodeVisitor`）实现精确的函数调用解析
- **Java** — 使用 javalang AST + 正则表达式检测方法调用；虚函数调用标记为 `is_polymorphic=True`
- **Kotlin** — 使用正则表达式进行方法调用检测

**输出格式：**
- `json` — 带符号和调用元数据的机器可读图数据
- `mermaid` — 按语言分组的文本流程图，边标注依赖类型
- `dot` — Graphviz DOT 格式，用于高质量静态渲染

---

## 爆炸半径

爆炸半径衡量变更在依赖图中传播的范围。

```
blast_radius_percent = (变更文件数 + 受影响的上游文件数) / 总文件数 * 100
```

- **0-20%**：低 — 变更隔离，可安全合并
- **20-50%**：中 — 影响适中，建议审查
- **50%+**：高 — 影响广泛，需仔细审查

示例：假设 `src/utils/helpers.py` 发生变更，项目共 17 个文件，其中 3 个文件依赖它：

```
blast_radius = (1 + 3) / 17 * 100 = 23.5%
```

---

## 风险评分

风险评分是一个透明的 0-100 分值，由五个加权分量计算得出：

| 分量 | 权重 | 说明 |
|------|------|------|
| `blast_radius_percent` | 50% | 受影响项目百分比 |
| `dependent_ratio` | 20% | 入度与总度之比 |
| `centrality_score` | 15% | 介数中心性 |
| `core_path_score` | 10% | 文件是否位于核心目录 |
| `cycle_penalty` | 5% | 文件是否参与循环 |

风险等级：
- **LOW**（0-29）：隔离变更，风险低
- **MEDIUM**（30-69）：影响适中，建议审查
- **HIGH**（70-100）：影响广泛，需仔细审查

每份风险报告都包含各分量明细和解释，使评分可审计而非黑箱。

---

## 扫描器限制

### Python 扫描器

Python 扫描器使用 `ast.parse()` 提取导入语句和符号，有以下限制：

- **宏展开的导入**（如 `if sys.version_info >= (3, 10):` 内的导入）不会被求值——扫描器读取的是原始源代码文本
- **动态导入**（`__import__()` 或 `importlib.import_module()`）不会被检测
- **命名空间包**（无 `__init__.py`）可能无法正确解析
- **第三方 stub 文件**（`.pyi`）会作为普通 Python 文件被扫描
- 符号提取仅限于顶层函数、类和类方法；嵌套函数和闭包被有意排除

### Java 扫描器

Java 扫描器使用 `javalang` 库进行 AST 解析，有以下限制：

- **Java 语言版本**：javalang 支持 Java 1.5+ 语法；较新的语言特性会发出警告但会被跳过
- **代码中的完全限定类型名**（如 `java.util.List list = new java.util.ArrayList()`）不会被跟踪为依赖
- **动态类加载**（`Class.forName()`、反射）不会被检测
- **生成的代码**（注解处理器、宏）不会被分析
- **符号级调用解析**是近似值——方法调用通过名称匹配解析，而非完整类型推断

### Kotlin 扫描器

Kotlin 扫描器使用正则表达式提取（javalang 不支持 Kotlin），有以下限制：

- **基于正则的解析**无法处理所有 Kotlin 语法变化；基于括号的类作用域跟踪对单行类定义有局限
- **Kotlin 特有构造**（扩展函数、DSL 构建器、重构类型参数）不会被语义分析
- **Koin/Kodein** 等依赖注入框架不会被跟踪
- **协程和 suspend 函数**被当作普通函数声明处理

### C/C++ 扫描器

C/C++ 扫描器使用正则表达式提取 `#include` 指令，有以下限制：

- **不会进行宏展开**——`#define` 宏、`#ifdef` 块和预处理条件不会被求值
- **不会使用编译器 include 路径**——扫描器仅搜索项目文件系统
- **不会识别 include 保护**（`#ifndef`）为依赖
- **系统头文件**通过尖括号引用（`#include <vector>`）被分类为外部/系统，不会解析到项目文件
- **有歧义的 include**（多个同名文件）保留未解析并附带警告，而非猜测
- **非标准 include 语法**可能无法识别

---

## 配置

DepPulse 会自动从项目根目录的 `deppulse.json` 加载配置。如果不存在配置文件，则使用默认值。

`deppulse.json` 示例：

```json
{
  "ignore_dirs": ["node_modules", ".venv", "build", "dist"],
  "ignore_files": ["*.generated.py", "*.pb.h", "*.min.js"],
  "include_dirs": ["include", "src"],
  "risk": {
    "high_threshold": 70,
    "medium_threshold": 30
  }
}
```

默认忽略的目录：`.git`、`__pycache__`、`.venv`、`venv`、`node_modules`、`build`、`dist`、`.idea`、`.vscode` 等。

---

## 缓存

DepPulse 将扫描结果缓存到 `.deppulse/cache.json`，避免重复解析未变更的文件。

- **缓存命中**：文件 mtime 和大小与缓存条目匹配——复用结果
- **缓存未命中**：文件被修改——重新扫描
- **`--no-cache`**：完全禁用缓存
- **`--incremental`**：结合 git diff 和缓存实现快速重新扫描
- **`--since "REF"`**：仅扫描自某个 git 引用或日期以来变更的文件
- **损坏的缓存**：静默忽略并重建

文件修改时间或大小变更时，缓存会自动失效。

---

## CI 集成

### GitHub Actions

DepPulse 通过 SARIF 输出格式与 GitHub Actions 集成：

```yaml
- name: 运行 DepPulse 扫描
  run: |
    pip install deppulse
    deppulse scan ./ --sarif-output deppulse-results.sarif

- name: 上传 SARIF 结果
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: deppulse-results.sarif
```

完整 workflow 示例见 [`.github/workflows/deppulse-ci.yml`](.github/workflows/deppulse-ci.yml)，包含高风险依赖检测逻辑。

### Pre-commit Hook

DepPulse 可通过 `.pre-commit-hooks.yaml` 格式作为 pre-commit hook 使用：

```yaml
# .pre-commit-hooks.yaml
- id: deppulse-scan
  name: DepPulse 依赖扫描
  description: 扫描危险的依赖变更
  entry: deppulse scan --json-output .deppulse/scan.json
  language: system
  files: '\.(py|java|kt|cpp|h|hpp)$'
  pass_filenames: false
  stages: [pre-commit, push]
```

---

## 输出格式

### JSON

结构化输出，用于程序处理：

```bash
deppulse scan ./ --json
deppulse report ./ --json-output report.json
```

### Markdown

人类可读的审计报告：

```bash
deppulse report ./ --markdown-output audit.md
```

### SARIF 2.1.0

用于安全和质量工具的机器可读格式（GitHub Code Scanning、VS Code 等）：

```bash
deppulse scan ./ --sarif-output report.sarif
deppulse report ./ --sarif-output report.sarif
```

SARIF 映射关系：

| DepPulse 概念 | SARIF 映射 |
|---|---|
| `ResolvedDependency`（内部） | `result.level = "note"` |
| `ResolvedDependency`（外部） | `result.level = "warning"` |
| `ResolvedDependency`（未解析） | `result.level = "error"` |
| `DependencyKind` | `ruleId`（如 `java_import`、`kotlin_import`） |
| `RiskReport` | `results[].properties.risk_*` |
| `CycleInfo` | `results[].ruleId = "dependency-cycle"` |

### Mermaid 流程图

可嵌入 Markdown 的文本图表：

```bash
deppulse viz ./ --format mermaid
```

### HTML 仪表盘

使用 D3.js 的交互式力导向图（单文件，无需构建）：

```bash
deppulse viz ./ --format html --output dashboard.html
```

功能：按语言着色节点、按入度调整大小、支持缩放/平移/拖拽、悬停提示、节点详情面板。

---

## 开发

```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试
pytest -q

# 详细测试输出
pytest -v

# 运行 linter
ruff check deppulse/

# 格式化代码
ruff format deppulse/
```

### 项目结构

```
deppulse/
├── __init__.py          # 版本信息
├── __main__.py          # python -m deppulse 入口
├── cli.py               # argparse CLI，所有子命令
├── config.py            # 配置加载
├── cache.py             # 文件缓存
├── git.py               # 通过子进程集成 git
├── models.py            # 所有数据类（类型定义）
├── reporting/
│   ├── __init__.py     # 从 legacy 和 sarif 重新导出
│   ├── legacy.py        # JSON 和 Markdown 报告生成
│   └── sarif.py         # SARIF 2.1.0 输出生成
├── core/
│   ├── orchestrator.py  # 使用 networkx 构建图、扫描器注册
│   ├── analyzer.py      # 影响分析和爆炸半径
│   ├── risk.py          # 透明的风险评分
│   ├── cycles.py        # 循环检测
│   └── callgraph.py     # 符号级调用图构建器
├── scanners/
│   ├── base.py         # BaseScanner 抽象类（策略模式）
│   ├── python_scanner.py # Python AST 扫描器
│   ├── cpp_scanner.py   # C/C++ 正则扫描器
│   ├── java_scanner.py  # Java javalang AST 扫描器
│   └── kotlin_scanner.py # Kotlin 正则扫描器
└── ui/
    ├── render.py        # Rich 终端渲染
    └── visualize.py     # Mermaid、DOT、HTML 可视化

tests/
├── fixtures/
│   ├── python_project/   # 带循环和语法错误的 Python 测试夹具
│   └── mixed_project/    # 混合 Python + C/C++ 测试夹具
├── test_python_scanner.py
├── test_cpp_scanner.py
├── test_java_scanner.py
├── test_kotlin_scanner.py
├── test_orchestrator.py
├── test_analyzer.py
├── test_callgraph.py
├── test_git.py
├── test_risk.py
├── test_cache.py
├── test_cycles.py
├── test_incremental.py
└── test_sarif.py
```

### 新增扫描器

实现 `BaseScanner` 抽象类即可：

```python
from deppulse.scanners.base import BaseScanner

class MyLangScanner(BaseScanner):
    name = "mylang"

    def can_scan(self, path: Path) -> bool:
        return path.suffix == ".ml"

    def scan(self, file_path: Path, project_root: Path,
             file_index: dict[str, Path] | None = None) -> ScanResult:
        # ... 提取依赖、解析、提取符号 ...
        return ScanResult(...)
```

然后在 `deppulse/core/orchestrator.py` 中注册：

```python
_SCANNER_REGISTRY = [
    PythonScanner(),
    CppScanner(),
    JavaScanner(),
    KotlinScanner(),
    MyLangScanner(),  # <-- 在此添加
]
```

无需修改其他代码。

---

## 路线图

以下功能已在 v0.2.0 中完成：

- **Java/Kotlin 扫描器** — 使用 javalang（Java）和正则（Kotlin）提取 JVM 语言的 `import` 和 `package` 语句
- **符号级调用图** — 将依赖分析扩展到函数/方法调用关系
- **CI 集成** — GitHub Actions workflow 和 pre-commit hook
- **SARIF 输出** — 用于安全和质量工具的机器可读格式
- **增量 diff 模式** — 通过 `--incremental` 和 `--since` 仅重新扫描变更文件

计划在后续版本中实现：

- **Web UI 仪表盘** — 交互式依赖可视化（v0.2.0 已提供基础 HTML + D3.js）
- **JavaScript/TypeScript 扫描器** — 基于 AST 的 import/export 提取
- **依赖健康指标** — 不稳定性、抽象性到主序列的距离（DAM）

---

## 许可证

MIT 许可证
