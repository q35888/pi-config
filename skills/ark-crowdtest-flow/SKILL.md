---
name: ark-crowdtest-flow
description: 方舟(火山引擎)模型众测全流程。当评审员参与方舟开发者众测(ark.volcengine.com/autocase)或类似大模型Coding能力众测时使用：读任务书→本地搭带缺陷的复杂靶子项目→上传创建任务→观察匿名模型→下载产物本地验收→有人味差异化评分。触发词：去方舟众测/测模型/众测打分/给任务书链接/给ark项目URL。
---

# 方舟模型众测全流程

## When to Use
评审员要参与"方舟开发者众测"(ark.volcengine.com/autocase) 或类似大模型 Coding 能力众测/评测时。典型触发：评审员给众测任务书链接(飞书)、ark 平台项目 URL、或说"去方舟众测""测模型""众测打分"。核心是让被测匿名 Coding 模型在本地预制的复杂项目上做"补测试+修bug"，再本地验收+评分。工作目录 `~/Desktop/ark-crowdtest/`，项目记忆见该目录 AGENTS.md。

## Procedure
1. 读规则：打开飞书任务书链接，虚拟滚动要异步分段 await 抓 innerText。提取：任务数与每任务轮次门槛(如3任务档150+轮)、harness(云端ClaudeCode/OpenClaw/本地ClaudeCode)、红线、质检要求(区分度/禁AI打分/匿名不猜身份)。
2. 登录ark：导航 `https://ark.volcengine.com/autocase/region:cn-beijing/crowdtest/myprojects/<项目ID>`。跳登录页则 browser_wait_human 请评审员本人登(账号只有他有)。确认待测模型数/最低任务数/harness。
3. 设计任务(合规第一)：必须真实工程改造类，禁长文本创作/凑token/伪装简单/竞赛纯耗token/合规风险。去掉工具任务不成立。推荐"为现成复杂项目补单测+修暴露bug"。
4. 本地预制靶子：在 `~/Desktop/ark-crowdtest/` 下准备载体(已有 sqlitedb-template：迷你关系数据库，lexer/parser/planner/storage/btree/WAL/executor/api，~3000行，基线50-75%覆盖，埋真实缺陷)。每期可基于模板改/换主题。**本地验证一律用独立 venv**(见Pitfalls)，在项目目录建 `.venv` 后 `pip install -e .[dev]` + pytest 验基线，再 zip 打包(打包时排除 .venv)。
5. 创建任务：点新建任务，选 harness(推荐云端ClaudeCode)，填标题/Prompt/Rubrics/上传zip。写作去AI味(见Pitfalls)。创建前贴给评审员过目，由他本人点创建任务。
6. 等就绪：创建后"打开终端"按钮先 disabled，sleep 60 刷新，enabled 即就绪。3 模型并行跑。
7. 观察模型：只用 Session 轨迹标签(不要 Terminal，xterm 对 snapshot 不友好)。看工具调用次数/tokens/时长/调用链。自主跑别打扰。
8. 验证产物(关键，最易出错)：Workspace 标签 hover 根目录点"下载文件夹"按钮下到本地。CDP 下载常失效则请评审员真实浏览器下载(默认 `~/Downloads/workspace/`，也可能下成 zip 在 `~/Downloads/`)。**拿到产物第一步：解压所有 zip + 和原模板做 diff 对比，看实际改了哪些文件、新增了什么，绝不能只看 workspace 顶层显示就下结论**——产物可能藏在 `.autocase/input/` 子目录或 zip 里。然后**在产物目录建独立 venv**(`python3 -m venv .venv && source .venv/bin/activate`)，`pip install -e .[dev]` → pytest → `pytest --cov --cov-report=term` → mypy。再把它声称修的bug在原项目复现+改后项目验证。功能测试要用**该模型自己的 API 入口**(各模型 dump/连接等函数名可能不同，先 grep 它的代码和测试看怎么调，别用一个标准误判)。
9. 判断公平性：多个模型在同一场景失败时，先在**原模板**上复现，确认是引擎基线既有问题还是模型引入的。基线既有 bug(如整数列存字符串、带索引整数查询崩)不计入本任务扣分，换避开它的方式(如用 TEXT 列)做公平专项测试。
10. 性能类任务额外：实跑模型自带的 benchmark 脚本验真(别信报告自述数字)。鉴别 benchmark 方法论——`optimizer_enabled` 开关在同二进制内对比最靠谱；`git checkout` 切基线可能切不干净导致 before/after 数字一样、提速造假。跨平台留意：模型报告 Linux 测的，mac 上 fcntl/某些 syscall 可能失效，区分是模型 bug 还是环境差异。
11. 生成验收截图：**必须用真实终端运行输出，不能是手写文字总结**（评审员原话：自己整理的总结图"没意义"）。实跑 `pytest --cov=... --cov-report=term` / `mypy` / bug 复现脚本，把真实命令+输出捕获成 txt，再用 PIL 渲染**黑底终端风格** PNG（标题栏+红黄绿圆点，`$`提示符绿、passed/Success/✓ 绿、✗ 红、TOTAL/修复数黄；等宽+中文字体 STHeiti Medium `/System/Library/Fonts/STHeiti Medium.ttc`，绝不能用 Menlo 等纯西文字体否则中文方块）。存 `~/Desktop/<模型>_验收截图.png`(别放 /tmp)。上传评分面板的"添加图片"(系统对话框评审员选文件)。
12. 评分：执行过程/执行产物各 0-10。要区分度：好8-10/一般5-7/差2-5，别全一样。依据写真人语气引真实数据。评审员本人点提交评测。

## Pitfalls
- **验收截图必须用真实终端输出**（评审员原话：手写文字总结图"没意义"）：实跑 pytest/mypy/bug复现 捕获命令+输出 txt，PIL 渲染黑底终端风格。不是自己整理的总结文字、不是手画的表格。
- **第38期起 4 个匿名模型**（往期 3 个），评分工作量 ×4，更要主动拉开区分度。
- **产物嵌套路径**：下载的 workspace 产物是 `workspace/<项目名>/<项目名>/`（嵌套一层），模型改后的代码在此；`.autocase/input/` 是原始上传 zip（非模型产物，别误判）。
- **下载文件名含 runtimeID**：`<任务ID>-<runtimeID>-<模型代号>.zip`（如 `3650-10401-quarry.zip`），runtimeID 对应评测页 URL `agentruntime/<id>`，可据此把产物和评测页对上号。
- **评分页 3 个 textarea**：执行过程依据 / 执行产物依据 / 验收说明（第三个，placeholder 明确要求"上传验收过程的相关截图"）。每个维度有独立"添加图片"按钮，系统对话框由评审员操作；CDP 点 ref 常失效（滚动后 ref 变），直接 wait_human 请评审员上传更稳。
- **写作去 AI 味（评审员反复强调的硬要求，否则触发"使用AI打分反馈"红线被质检识别）**：
  - 不用括号做行内注释（彻底不用，prompt 和评分依据都不行——如"8 个源码（lexer、cache）""7/2 仍 3.0"这种括号补充会被评审员点名是 AI 腔，要把信息直接融进句子或用逗号衔接）
  - 不用横线 bullet list 或 1.2.3. 编号堆砌
  - 禁止这些 AI 腔句式："一X就怎么怎么样"(总结腔)、"我已经完成了XX任务"(汇报腔)、"在XX地方做了XX"(机械陈述路径)、"已按平台要求把XX下载到本地（路径）建独立venv验收"这种流程汇报
  - 评分依据直接讲观察到的事实、具体的坑、自己的判断，带点口语和不完美，别端着别工整；别交代"我在哪建venv""我怎么验收"这种过程，只讲它实际跑成什么样、哪好哪不行
- **绝不凭 workspace 顶层显示下结论（本轮最大教训）**：产物可能藏在 `.autocase/input/` 子目录或下载的 zip 里，workspace 网页顶层只显示上传的原始 zip。必须解压所有 zip + 和原模板 diff 对比，看实际改了哪些文件。anchor 任务3 曾因只看顶层被误判"零产出"差点打 2 分，实际改了 7 个核心文件是高质量产出。
- **平台工具调用次数统计不准**：不能用它判断轮次是否达标。anchor 任务3 显示 31 次/13 小时，但实际产出完整代码。以代码实际产出为准，不以平台统计为准。
- **功能测试要用模型自己的 API 入口**：dump 等功能各模型函数名/调用方式可能不同（dump_database 模块级 / iterdump / con.dump() 方法）。先 grep 模型的代码和测试文件看它怎么调，用它的方式测，别用一个统一标准误判。
- **benchmark 方法论鉴别**：性能任务别信报告自述数字，实跑模型自带 benchmark。`optimizer_enabled` 开关在同二进制内对比最靠谱；`git checkout` 切基线可能切不干净导致 before/after 数字几乎一样、提速造假。
- **基线 bug 公平性**：多模型同场景失败时，先在原模板复现，确认是引擎基线既有问题还是模型引入的。基线既有（如整数列存字符串、带索引整数查询崩）不计本任务扣分，换避开它的方式做公平专项测试。
- **跨平台留意**：模型报告 Linux 下测的，mac 上 fcntl 等可能失效。区分是模型 bug 还是环境差异，别一概扣分。
- **绝不污染全局 Python 环境(评审员硬要求)**：本地跑任何靶子项目/验收产物，一律先在项目目录建独立 venv(`python3 -m venv .venv && source .venv/bin/activate`)再 pip install。绝不往全局 miniconda/system site-packages 装。全局装多了不同项目依赖会版本冲突、环境越来越乱。验收完可直接删 .venv。
- Terminal(xterm)对自动化极不友好，一律用 Session 轨迹标签。
- CDP Chrome 点"下载文件夹"常无反应(没配下载目录)，请评审员真实浏览器下载。默认存 `~/Downloads/workspace/`。
- 验收截图别放 /tmp(macOS 隐藏)，放 ~/Desktop 起中文名。
- 埋bug参考-词法：`""`(空串) in `"eE"` 为 True 致数字末尾误判 float；单字符op集合漏 `<>!` 致比较运算符报错；关键字集合漏 IF/REPLACE 致 IF NOT EXISTS/OR REPLACE 解析失败。
- 埋bug参考-存储：删行用 list.pop 移 slot 致 row_index 失效(墓碑更稳，可故意留 pop 版)；WAL 的 drop_table 只存 schema 致 ROLLBACK 丢数据。
- 埋bug参考-语义：SUM 空集返回0非NULL；NULL AND FALSE 应 FALSE 非 NULL；CREATE UNIQUE INDEX 的 UNIQUE 没 consume 致永远 False；qualified 列 ref 在 JOIN 因 env 键冲突解析错。
- 匿名合规：Session 轨迹可能暴露模型系统prompt/身份/billing header。严格不按身份评分，依据里绝不提模型真身。
- 评分区分度：质检查"无区分度"和"偏序与大盘显著不同"。3 模型拉开分差，都不错也要分档。
- 本地验收必须真做：平台要求下载本地执行验收+上传截图。不能只看模型自述，必须本地独立跑 pytest/coverage/mypy 复现+抽验bug有效。
- 提交权在评审员：创建任务和提交评测都由评审员本人点。agent 只填内容和过目。

## Verification
1. 靶子基线OK：cd 项目 && `pip install -e .[dev]` && pytest 全绿，覆盖率 50-75%，核心功能端到端跑通。
2. 埋的bug可复现：≥3-5个原项目上报错或异常(最小用例)，但基线测试不触发(保持通过)。
3. 任务创建后：列表出现该任务，进度从 0/3 增长，打开终端从 disabled 变 enabled。
4. **产物必须解压+diff**：解压所有 zip，和原模板 diff 看实际改了哪些文件/新增什么，不以 workspace 顶层显示为准。
5. 产物本地验收三件套全过：pytest 全绿 & 覆盖率达标(≥90%或显著提升) & mypy 零报错，数据与模型自述一致。
6. bug修复抽验：抽 ≥3 个 bug，原项目复现存在 + 改后项目验证修好 + 原测试没改坏。功能测试用模型自己的 API 入口。
7. 公平性：多模型同场景失败时，确认是基线既有 bug 则不计扣分。
8. 性能任务额外：实跑模型自带 benchmark 验真，数据与报告一致；benchmark 方法论靠谱(optimizer_enabled 开关 > git checkout)。
9. 评分有区分度：3 模型分数有合理差异，依据引本地实跑真实数据，文本去AI味。
