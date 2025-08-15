# CMakeLists.txt 转换为 Android.bp

本仓库包含一个 Python 3.6 兼容脚本，用于将 `CMakeLists.txt` 转换为 Soong/Android 的 `Android.bp` 基本构建描述。

## 文件
- `cmake_to_bp.py`: 转换脚本（Python 3.6 兼容）
- `Android.bp`: 由脚本根据当前 `CMakeLists.txt` 生成的结果

## 环境要求
- Python 3.6 及以上

## 使用方法
在仓库根目录执行：
```bash
python3 cmake_to_bp.py --in CMakeLists.txt --out Android.bp
```
脚本会读取 `CMakeLists.txt`，解析常见 CMake 指令并生成对应的 `Android.bp`。

## 支持的 CMake 指令（子集）
- `add_library`
- `add_executable`
- `target_sources`
- `target_include_directories`（支持 `PUBLIC`/`PRIVATE`/`INTERFACE`，展开简单 `$<...:...>` 生成器表达式）
- `target_link_libraries`（将 `ns::name` 转为 `name`；别名 `add_library(A ALIAS B)` 会解析到真实目标）
- `target_compile_definitions`（转为 `cflags` 中的 `-D...`）
- `target_compile_options`（转为 `cflags`）
- `set_target_properties(... PROPERTIES CXX_STANDARD <ver>)`（转为 `cpp_std: "c++<ver>"`；默认 `c++17`）

## 生成规则与约定
- 目标类型：
  - `add_library` -> `cc_library`
  - `add_executable` -> `cc_binary`
- 头文件路径：
  - 展开常见变量：`${CMAKE_CURRENT_SOURCE_DIR}`、`${CMAKE_BINARY_DIR}`
  - 简化 `include/${PREFIX}` 为 `include`
  - 过滤变量占位符，保留相对路径
- 依赖：
  - 统一以 `shared_libs` 输出（如需区分静态/共享，可手动调整为 `static_libs`/`shared_libs`）
- 编译宏与选项：
  - `target_compile_definitions` 和 `target_compile_options` 合并至 `cflags`

## 已知限制
- 未自动迁移自定义步骤（如 `add_custom_command`/`add_custom_target`，例如 IDL 生成），如需请在 Soong 中以 `genrule` 或自定义模块补充。
- 未解析复杂的生成器表达式与条件逻辑（例如复杂 `if()`/`elseif()` 分支）。
- 未自动处理安装与导出（`install()`/`export()` 等）。

## 手动调整建议
- 如果某些第三方库在 AOSP/Soong 中模块名不同，请在 `shared_libs` 中按需替换为正确的 Soong 模块名。
- 若需要静态链接，调整对应目标的 `static_libs`/`shared_libs`。
- 若需要支持不同的 `cpp_std` 或特定平台宏，请在各目标的 `cflags` 中补充。

## 复现生成
当前 `Android.bp` 由以下命令生成：
```bash
python3 cmake_to_bp.py --in CMakeLists.txt --out Android.bp
```

---
如需扩展脚本解析范围（例如支持更多 CMake 指令），可在 `cmake_to_bp.py` 中相应增加解析逻辑并重新生成。

## 生成说明
本 README、`Android.bp` 及 `cmake_to_bp.py` 由 Cursor 辅助生成与整理。

## 生成说明（追加）
Python 文件 `cmake_to_bp.py` 由 Cursor 生成。