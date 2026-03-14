# Vertex Gemini 集成说明

## 1. 这次分支改了什么

这次分支的目标是把 VAT 的 LLM 接入从“只能走 OpenAI-compatible / Vertex API key 最小支持”补到“正式支持 Vertex Native 的两种认证模式”，同时不改上层翻译、断句、视频信息翻译的调用方式。

本次改动包括：

- `vat/llm/client.py`
  - `vertex_native` 现在支持两种认证：
    - `auth_mode=api_key`
    - `auth_mode=adc`
  - `adc` 路线使用 `google-auth` 获取 Bearer token
  - 继续把 Vertex 返回适配为 `response.choices[0].message.content`
- `vat/config.py`
  - 新增全局配置：
    - `llm.auth_mode`
    - `llm.project_id`
    - `llm.credentials_path`
  - `LLMConfig.is_available()` 现在会按认证模式判断可用性
- `config/default.yaml`
  - 补充 Vertex 的新配置项
- `tests/test_llm_client_vertex.py`
  - 新增 ADC 路线测试
- `tests/test_config.py`
  - 新增 Vertex ADC 配置可用性测试
- `tests/test_vertex_translation_flow.py`
  - 新增从 `Config -> LLMTranslator -> Vertex -> translated.srt` 的集成测试
- `vat/llm/readme.md`
  - 补充 Vertex Native 的使用说明

## 2. 为什么这样改

项目当前已经有统一的 `call_llm(...)` 抽象。相比强行把 Vertex 包成 OpenAI-compatible，直接在 client 层对 Vertex Native 做适配更清楚，也更容易把 API key 和 ADC 两条路一起支持掉。

这样做的结果是：

- 上层业务不需要知道 provider 差异
- 现有翻译器、断句、场景识别调用方式不变
- API key 可以继续用于快速验证
- ADC 可以作为长期和正式部署方案

## 3. 现在支持的 Vertex 接入方式

### 3.1 API key

配置示例：

```yaml
llm:
  provider: "vertex_native"
  auth_mode: "api_key"
  api_key: "${VAT_VERTEX_APIKEY}"
  model: "gemini-2.5-flash"
  location: "global"
```

请求路径：

```text
https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent?key=...
```

特点：

- 配置最简单
- 最适合当前 VAT 的快速迁移与开发验证

### 3.2 ADC

配置示例：

```yaml
llm:
  provider: "vertex_native"
  auth_mode: "adc"
  model: "gemini-2.5-flash"
  location: "global"
  project_id: "vertex-490203"
  credentials_path: "/home/gzy/.ssh/vat_vertex.json"
```

请求路径：

```text
https://aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent
```

特点：

- 更符合 Google 官方推荐的正式认证方式
- 更适合长期部署

注意：

- `credentials_path` 建议始终写绝对路径
- 这台机器之前的 `GOOGLE_APPLICATION_CREDENTIALS=.ssh/vat_vertex.json` 是相对路径，容易在不同工作目录下失效

## 4. 这次没有做的事

以下内容目前还没有纳入默认实现：

- `streamGenerateContent` 作为默认路径
- Vertex thinking 参数控制
- `usageMetadata` 透传给上层

原因：

- VAT 当前是批处理翻译场景，不需要边生成边消费
- `streamGenerateContent` 返回结构和当前统一抽象不一致
- thinking 在 Gemini 2.5 上的 token 开销明显偏高，后续应单独控制，不适合这次顺手改语义

## 5. 当前推荐方案

### 开发和现阶段使用

默认推荐：

```yaml
llm:
  provider: "vertex_native"
  auth_mode: "api_key"
```

原因：

- 实现最简单
- 真实联调已经验证可用
- 最符合“少改代码、快速切换”的目标

### 长期部署

长期推荐：

```yaml
llm:
  provider: "vertex_native"
  auth_mode: "adc"
```

原因：

- 更符合官方建议
- 认证模型更规范

## 6. 本次验证

### 单元与集成测试

执行：

```bash
HOME=/tmp pytest tests/test_llm_client_vertex.py tests/test_config.py tests/test_translator_error_handling.py tests/test_vertex_translation_flow.py -q
```

结果：

- `39 passed`

### 真实流程验证

实际跑通了两条最小翻译流程：

- API key
  - 输入：`おはようございます`
  - 输出：`早上好`
  - 产物：`/tmp/vat-e2e-api-key/translated.srt`
- ADC
  - 输入：`こんばんは`
  - 输出：`晚上好`
  - 产物：`/tmp/vat-e2e-adc/translated.srt`

这说明当前改动已经能从 VAT 配置与翻译器调用链一路走到真实字幕文件产出。

## 7. 后续建议

- 给 Vertex 增加 thinking 控制，降低 `thoughtsTokenCount`
- 如果后续 Web UI 有流式显示需求，再单独评估 `streamGenerateContent`
- 若后续需要统计成本或调试 token 消耗，再考虑把 `usageMetadata` 暴露出来
