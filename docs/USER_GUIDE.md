# 使用说明

## 1. 运行环境

NMA 当前版本面向 macOS，本地运行。源代码构建后的应用通过 Python 后台、Cocoa/WKWebView 原生窗口以及本机 MATLAB/SPM12/CAT12 环境协同工作。

## 2. 数据入口

系统支持 Excel 批量导入和单病例手动录入。病例编号用于匹配患者数据目录与 CAT12 结果目录。影像可以从已有 CAT12 结果、已有 NIfTI 或原始 DICOM 开始。

## 3. 分析流程

任务由七个阶段组成：环境检查、患者信息读取、影像匹配或转换、CAT12 处理、体积指标提取、MRI 三切面生成、PDF 报告生成。若存在可复用 CAT12 结果，系统会跳过不必要的重复计算。

## 4. 体积结果

首页展示 TIV、GM 和 WM。v0.11.1 对 TIV 增加一致性校验；当直接读取的 TIV 数值尺度异常时，会结合 CSF、GM 与 WM 的绝对体积进行校验。

## 5. 配置

首次运行时在设置中心配置 MATLAB、SPM12、CAT12、Python、dcm2niix、患者数据目录与 CAT12 输出目录。macOS 保护目录可通过界面的“选择/授权”操作授予访问权限。

## 6. 构建

在项目根目录运行：

```bash
bash scripts/build_macos_app.sh
```

生成的应用位于 `dist/NeuroMorph Assessment.app`。
