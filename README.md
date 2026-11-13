# Google Drive 分批下载工具

启动后可输入 Google Drive 文件夹共享网址；回车使用 `config.json` 中的默认文件夹。默认下载链接文件夹中的全部文件。也可在配置中指定 Excel 或 CSV 名单，按 `Missing_File` 列顺序筛选。每个未开始的批次会询问下载数量，可输入**任意正整数，不设上限**，回车沿用上次选择（首次默认 100）。输入数量超过剩余文件数时，下载全部剩余文件。始终选 100 时共 10 批；修改数量后，总批次数会相应变化。

当前电脑已创建的 rclone 连接名是 `whisper.shi218@gmail.com`，`config.json` 已与之匹配。下文 `gdrive` 是新建连接时的示例名称。已创建连接的情况下，保持 v2rayN 运行，直接双击 **reconnect_drive.bat** 重新完成浏览器授权，再运行 **start_download.bat**，无需重新创建客户端 ID。

## 在新电脑上用 git 克隆

仓库只包含程序、脚本和说明；不含下载的文件、进度、日志、rclone 程序和本机配置。

```powershell
git clone https://github.com/Whisper218/GoogleDriveDownloadTool.git
cd GoogleDriveDownloadTool
copy config.example.json config.json
```

`config.json` 是本机配置，已在 `.gitignore` 中排除，不会被提交。若没有 `config.json`，程序会暂时使用 `config.example.json` 并提示。编辑 `config.json` 中的 `remote`、`folder_id`、`proxy`、`batch_size` 后再按下面步骤运行；`folder_id` 也可以不填，启动时直接粘贴文件夹网址。

1. `install.bat`：创建 `.venv`、安装依赖并下载 rclone 到 `tools`。
2. `configure_drive.bat`：用你自己的 Google 账号授权，连接名要和 `config.json` 的 `remote` 一致。
3. `start_download.bat`：开始分批下载。

## 第一次使用

1. 双击 **install.bat**：创建项目内 `.venv`，安装 openpyxl，从 rclone 官网下载 Windows 版本并核对官方 SHA256 校验和。需要联网；已安装的 rclone 不会被自动更新。若没找到 Python，请先安装 [Python 3.10 或更高版本](https://www.python.org/downloads/windows/)。
2. 双击 **configure_drive.bat**，按下面说明配置名为 `gdrive` 的 Google Drive 连接。这一步需要你登录有权限访问共享文件夹的 Google 账号。
3. 双击 **start_download.bat**，粘贴 Google Drive 文件夹网址；按提示输入本批下载数量，例如 `50`；直接回车使用默认值。下载期间显示当前批次、文件序号、传输速度和传输进度。
4. 每批完成后，先处理或备份 `download\batch_0001` 等目录中的文件。程序此时等待输入，不会自动继续。
5. 输入 **`DELETE 1`** 并回车，确认处理完第 1 批并继续；本地文件不会被程序删除。后续输入 `DELETE 2`、`DELETE 3` 等。输入 `q`、其他文字或直接回车，将保留文件并退出；在数量输入处输入 `q` 同样可以退出。

最后一批同样等待确认。希望保留最后一批时输入 `q` 即可；下次启动仍停在这一批。所有批次确认处理完后，重启只会报告已完成，不会重复下载。

## 配置 Google Drive

在 `rclone config` 中依次选择：

- `n`：新建连接；名称输入 `gdrive`。
- 存储类型输入 `drive`。
- `client_id` 和 `client_secret` 使用你自己的 Google OAuth 客户端。当前官方文档提示内置共享客户端将在 2026 年停止工作，建议参照 [rclone 的 OAuth 客户端配置说明](https://rclone.org/drive/#making-your-own-client-id) 创建自己的客户端。
- scope 选择 **`drive.readonly`**（只读文件内容和元数据，通常是选项 2）。
- 使用个人登录时，service account 留空；高级配置通常选否；自动浏览器登录选是，并使用有共享文件夹权限的账号完成授权。
- 本来源是共享文件夹链接，并不等于 Shared Drive/Team Drive；普通共享文件夹在该问题选择否。
- 保存连接并退出。

下载器通过 `--drive-root-folder-id 1xZRH-PAlv9FNBp-EuCv92ZmLXz4hm2OM` 直接定位来源，不需要把文件夹复制到“我的云端硬盘”。此链接不能代替 Google 授权。若创建了其他名称的连接，可修改 `config.json` 的 `remote`，不需要提供任何 token 给本程序。

rclone 的凭据保存在它自己的用户配置文件中。不要把该文件或 token 发给别人。修改 `--drive-scope` 本身不会撤销旧 token 的写权限；首次授权时就应选择只读 scope。本项目调用的云端操作仅有 `listremotes`、`lsjson` 和云端到本地的 `copyto`，没有云端删除、移动或同步操作。[官方只读权限说明](https://rclone.org/drive/#drive-readonly) / [copyto 文档](https://rclone.org/commands/rclone_copyto/)。

## 参数和资源限制

编辑 `config.json`：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `excel` | `null` | 可选 Excel 文件路径；不指定则使用全部来源文件 |
| `csv` | `null` | 可选 CSV 文件路径；与 excel 二选一，使用 `Missing_File` 列 |
| `sheet` | `null` | 使用活动工作表；可指定工作表名 |
| `column` | `Missing_File` | 第一行的文件名列标题 |
| `remote` | `whisper.shi218@gmail.com` | 与当前电脑已创建的 rclone 连接同名，不带冒号 |
| `proxy` | `socks5://127.0.0.1:10808` | 本机 v2rayN 代理；不用代理时设为空字符串 |
| `folder_id` | 已填入目标 ID | Google Drive 共享文件夹 ID；也可在启动时直接粘贴文件夹网址 |
| `batch_size` | `100` | 首次数量输入的默认值，可设任意正整数；以后回车沿用上次选择 |
| `attempts` | `3` | 单文件每轮最多尝试次数 |
| `retry_delay_seconds` | `10` | 自动重试间隔，指数退避，上限 60 秒 |
| `buffer_size` | `8Mi` | 单次传输缓冲区 |
| `max_buffer_memory` | `32Mi` | rclone 传输缓冲区总量限制，并非整个进程内存硬上限 |
| `bandwidth_limit` | `off` | 不限速；可改 `10Mi` 等 rclone 支持值 |
| `rclone` | `tools/rclone.exe` | 优先使用此路径，否则查找系统 PATH |

程序按 Excel/CSV 名单顺序或云端列表顺序串行处理，每次 1 个文件，1 个 checker，关闭多流传输；不递归索引整个 Drive，也不把大文件读入内存。rclone 的运行时和元数据仍会额外占用内存。Excel 中重复文件名（含仅大小写不同的名称）会报错，避免 Windows 文件覆盖；空白行忽略。列内只接受共享文件夹根目录中的文件名，不接受子目录路径或通配符。

也可在本目录终端运行：

```powershell
# 只检查本地配置和名单，不联网、不创建任务
.\.venv\Scripts\python.exe download_drive.py --plan

# 检查 rclone、Google 授权和来源文件夹，不下载
.\.venv\Scripts\python.exe download_drive.py --check

# 直接指定尚未开始的批次每批 50 个，省略交互询问
.\start_download.bat --batch-size 50

# 运行离线验证
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 换一个新的下载名单（新任务）

程序按来源文件和名单锁定任务，防止旧进度套用到新清单。要改用另一份 Excel/CSV 清单，先归档当前任务：

```powershell
.\start_download.bat --new-task
```

它会把 `download_state\progress.json` 另存为 `progress.archived.<时间>.json`，不删除任何已下载文件。若 `download` 目录仍有文件，程序会拒绝归档，请先备份或移走。归档成功后修改 `config.json` 的 `excel` 或 `csv`，再运行 `start_download.bat` 即开始新任务。

`config.json` 里的路径要写普通字符串，例如 `"E:\\files\\data\\list.xlsx"`。不要写成 Python 的 `r"..."` 形式；程序会尝试自动去掉多余引号，但仍以正确写法为准。

## 中断恢复和失败处理

### Google 授权换取令牌超时

若浏览器授权成功，但命令行在 `oauth2.googleapis.com/token` 连接超时，需让 rclone 同样使用代理。本项目已配置 v2rayN 的 `socks5://127.0.0.1:10808`，授权启动和下载启动均读取 `config.json` 的 `proxy` 字段，并为 rclone 设置 `HTTP_PROXY`、`HTTPS_PROXY`。本地授权回调通过 `NO_PROXY` 排除，不经过代理。

使用时保持 v2rayN 运行。如果代理端口改变，修改 `proxy`；不再使用代理时设为 `""`。更改后重新启动脚本才会生效。令牌交换失败后需要重新发起浏览器授权，不能继续使用旧授权码。已有连接直接运行 `reconnect_drive.bat`。如尚未创建连接则运行 `configure_drive.bat`，并使连接名称与配置中的 `remote` 一致。

- 每个文件成功下载并生成 SHA256 后，立即以临时文件加原子替换方式保存进度；同一项目只允许一个进程运行。
- 中断后重新双击相同启动文件即可。未完成的批次继续，已成功文件会先校验本地大小与 SHA256，不重复下载。若文件丢失或被修改，程序停止并保留现场，请恢复原文件。已在等待确认的批次可自行处理文件，不会被重新下载。
- 这是**文件/批次级恢复**。中断时正在传输的单个文件可能重新传输；不承诺任意大文件的字节级断点续传。rclone 默认通过临时文件下载和校验，未成功文件不会被记为完成。
- 自动重试耗尽后继续尝试本批剩余文件。本批尝试结束后，普通启动入口会显示选项：`r` 重试失败文件，`c` 保存失败清单并进入本批处理确认，`q` 或回车退出。重新启动已尝试完毕的失败批次时，同样先显示这些选项，不会强制重试，也不需要先联网。
- 如果确认时断电，重启后仍需要再次输入本批 `DELETE N`；程序不会因中断自动推进，也不会删除本地文件。
- 不要在已有任务中修改 Excel 文件列表、顺序或云端来源，也不要移走 `download_state`。程序会拒绝不一致的任务配置。批次大小请通过运行时输入调整；每批的具体文件范围会保存到进度中。已开始的批次（包括未完成、失败待重试、等待删除确认的批次）仍按原范围继续，下一批再询问数量。重启已经选择数量的批次时会复用已保存的范围。旧版进度可以直接继续，无需重置。
- 不要删除归属标记 `.batch-owner.json`；程序通过它和路径检查防止误删别的目录。检测到符号链接、目录联接或重解析点时会停止。

## 输出目录

### 本批有失败，但需要先继续下一批

1. 在原窗口输入 `q` 退出，避免两个程序同时运行。
2. 先上传本批已成功下载的文件，并确认上传成功。
3. 双击 `start_download.bat`，在失败批次菜单输入 `c`。也可以使用专门的 `continue_after_failures.bat` 入口。它会将当前失败文件永久另存到 `download_state\deferred_failures` 下的 JSON 和 CSV 清单，然后等待本批确认；暂缓文件不会自动补下。
   如果成功文件已经从本地移走或被修改，程序会额外要求输入 `UPLOADED N`（例如第 5 批输入 `UPLOADED 5`），确认本批成功文件已全部上传并确认成功。未确认时保留进度并退出，不会直接将丢失文件视为完成。
4. 输入对应的 `DELETE N`（例如第 4 批输入 `DELETE 4`），确认后进入下一批，本地文件均保留。输入 `q` 则退出并保留本批。

当前待解决失败清单是 `download_state\failed_files.csv`，每批前进后会刷新；暂缓清单不会随批次前进而清空。想先补下失败文件时，在原失败提示处输入 `r`，或者正常启动 `start_download.bat`，不使用暂缓入口。来源提示 `directory not found` 时，需检查源文件名、位置和访问权限，重复重试可能无法解决。

```text
download/
  batch_0001/                  当前批文件，确认前一直保留
    .batch-owner.json          本批归属标记
    S2_grid_114.tif
download_state/
  progress.json                文件校验值、批次状态及已确认批次历史
  failed_files.csv             当前仍失败的文件及原因，成功后从列表移除
  download.log                 批次、重试、错误和退出记录，长期追加
  rclone.log                   rclone 下载详情，长期追加
  run.lock                     防重复启动锁；进程退出后自动释放，文件可以留着
```

首批实际目录会在开始下载时创建。之前的失败历史保留在追加日志中；CSV 是当前待解决失败清单。处理完成的批次在 `progress.json` 的 `history` 中留有记录。

## 手动安装替代方式

如自动安装网络受限：安装 Python 3.10+；在本目录运行 `python -m venv .venv`，再运行 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。从 [rclone 官方下载页面](https://rclone.org/downloads/) 获取适合系统架构的 Windows 版本，解压并将 `rclone.exe` 放入本项目 `tools` 文件夹；然后按前述步骤授权并启动。
