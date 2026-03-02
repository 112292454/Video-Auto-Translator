# 翻译模型评测 & ASR质量评估 综合报告

**评测时间**：2026年2月
**评测目标**：评估国产模型替代Gemini做VTuber字幕翻译的可行性，以及ASR质量改进方向

---

## 一、背景

当前pipeline使用Google Gemini-3-flash做日语→中文字幕翻译，质量好但成本高。评测了以下替代方案：

| 方案 | 来源 | 月成本 | 独有模型 |
|------|------|--------|---------|
| 方舟 Coding Plan (Pro) | 火山引擎(字节) | ¥?/月(90k次) | deepseek-v3.2, doubao系列 |
| DashScope Coding Plan | 阿里云 | ¥7.9/月(18k次) | qwen3-max, qwen3.5-plus, glm-5, MiniMax-M2.5 |
| gpt-5-nano | videocaptioner中转站 | 按量付费 | gpt-5-nano |
| Gemini-3-flash | Google API | ~¥150/20k次 | Gemini-3-flash |

两个 Coding Plan 共有模型：kimi-k2.5, glm-4.7。跨厂商验证确认同模型质量完全一致。

---

## 二、评测方法

- 独立评测脚本 `scripts/translation_benchmark.py`，不走生产pipeline
- 逐条定性阅读评估（非机械指标），对照原文日语+Gemini翻译+测试模型翻译
- 中文版提示词和few-shot示例存放在 `scripts/translation_benchmark_prompts/`

### 测试视频（共12个）

| 视频ID | 内容类型 | 字幕条数 | 采样 |
|--------|---------|---------|------|
| _QOMPli80JA | 双人联动对话 | 155 | 全量 |
| czIBPN1eCbU | 美食实况 | 448 | 全量 |
| k8CCqKYx2Pk | 马里奥卡丁车(ASR差) | 234 | 全量 |
| i-EreP4zejg | 混合(歌词+叙事) | 892 | 100 |
| enAZ8wvvgl4 | 直播开箱 | 242 | 100 |
| oE8HLVwpimA | 多人正式公告 | 103 | 全量 |
| q2B_u_wZWAQ | 恐怖游戏直播 | 511 | 80 |
| X53mU_mxCDQ | 杂谈直播 | 406 | 80 |
| CMvE8F9INDM | 赛车30分挑战 | 84 | 80 |
| DoDiPWf0Rg4 | 恐怖游戏杂谈 | 401 | 80 |
| IEYQQQFmxak | 马里奥卡丁车REVENGE | 212 | 100 |
| ajda3lbz6Mk | 风来のシレン | 237 | 100 |

---

## 三、翻译参数优化

在 `_QOMPli80JA` 50条样本上系统测试了22+组配置：

| 因素 | 测试范围 | 结论 |
|------|---------|------|
| **提示词语言** | 英文/中文 | 中文推荐（更好），英文也可接受 |
| **Few-shot示例数** | 0/6/12/25+条 | **12条最优**：6→12提升显著，25+反而下降 |
| **翻译模式** | reflect/standard | **reflect更好**（更生动、语义错误更少） |
| **Batch size** | 10/30/50/100 | **质量无差异，BS越大越好**（减少API调用次数） |
| **Temperature** | 0.3/0.7/1.0 | 差异不大，默认1.0即可 |
| **Optimize** | 有/跳过 | 跳过影响微小，但保留为好 |
| **Thinking模式** | enabled/disabled | **应关闭**（对ASR纠错无帮助，慢50-100倍） |
| **混合方案** | Gemini opt+kimi trans | 无额外收益（Gemini纠错在translate阶段） |
| **ASR-aware prompt** | 告知模型输入是ASR | 无效（模型能力硬限制） |

**关于Batch size**：逐条阅读BS=30/50/100三组翻译，差异仅为随机措辞变化（如"诶"vs"咦"），无质量退化。BS越大API调用次数越少，推荐BS=100。

**关于Thinking模式**：用7个ASR乱码案例做AB对比，thinking=enabled对ASR纠错完全无帮助，且单条延迟从0.4-4.5s增至16-270s。ASR纠错是模型知识层面的差距，不是推理深度问题。

**关于reflect vs standard的修正**：初始实验用"Gemini完全一致率"得出standard>reflect的错误结论。逐条定性阅读后确认reflect更好——standard在美食实况视频中将拉面品牌"スミレ"翻译成"紫罗兰"（连续8条错误），reflect正确保留了品牌名。

---

## 四、模型横向对比

### 全模型排名（按翻译质量）

| 排名 | 模型 | 来源 | 翻译风格 | 速度(155条) | 推荐度 |
|------|------|------|---------|------------|--------|
| 1 | **kimi-k2.5** | 火山/DashScope | 最活泼可爱，VTuber味浓 | 176s | ⭐⭐⭐⭐½ |
| 2 | **deepseek-v3.2** | 火山独有 | 语义准确，风格偏正经 | 145s | ⭐⭐⭐⭐ |
| 3 | **qwen3-max** | DashScope独有 | 自然流畅，偶尔更准确 | ~180s | ⭐⭐⭐⭐ |
| 4 | qwen3.5-plus | DashScope独有 | 偏正式/平淡 | 慢 | ⭐⭐⭐ |
| 5 | doubao-seed-code | 火山独有 | 中规中矩 | 93s(50条) | ⭐⭐⭐ |
| 6 | gpt-5-nano | 中转站 | 平淡，招牌语丢失 | 快 | ⭐⭐⭐ |
| 7 | glm-5 | DashScope独有 | 可用但无特色 | 很慢 | ⭐⭐½ |
| 8 | doubao-seed-2.0-code | 火山独有 | 最快但有错译风险 | 59s(50条) | ⭐⭐½ |
| 9 | MiniMax-M2.5 | DashScope独有 | optimize无效，翻译勉强 | 快 | ⭐⭐ |
| - | glm-4.7 | 火山/DashScope | 可用但太慢(668s/50条) | - | ⭐ |
| - | kimi-k2-thinking | 火山独有 | 翻译任务失败 | - | ❌ |
| - | auto | 火山 | 不支持Coding Plan | - | ❌ |

### kimi-k2.5 vs deepseek-v3.2 关键差异

两者都是火山引擎 Coding Plan 的推荐模型，风格取向不同：

| 案例 | DeepSeek | Kimi | 判定 |
|------|----------|------|------|
| #72 ツンデレセリフ(傲娇台词) | "来段傲娇台词！" ✅ | "来段傲娇自拍吧～" ❌(误解为selfie) | DeepSeek |
| #148 ガチャ(开门声) | "咔嚓（开门声）啊" ✅ | "是抽卡呀——" ❌(误解为gacha) | DeepSeek |
| #3 招牌语 | "空空狐狸～" ✅ | "狐狐——狐狸——" △ | DeepSeek |
| #123 モコ(气鼓鼓) | "已经是Moko了" △(直译) | "气鼓鼓了哟～" ✅(生动) | Kimi |
| 傲娇台词整体风格 | 自然流畅，偏正经 | 更活泼可爱，VTuber味浓 | 看需求 |

| 维度 | DeepSeek-v3.2 | kimi-k2.5 |
|------|--------------|-----------|
| 语义准确性 | ⭐⭐⭐⭐ | ⭐⭐⭐½ |
| VTuber风格/活泼度 | ⭐⭐⭐½ | ⭐⭐⭐⭐½ |
| 速度 | ⭐⭐⭐⭐½ | ⭐⭐⭐⭐ |
| ASR纠错 | ⭐⭐ | ⭐⭐ |

**选择建议**：追求VTuber风格→kimi-k2.5；追求语义准确/速度→deepseek-v3.2。

### nano vs kimi-k2.5 对比（6个视频，470条）

| 维度 | gpt-5-nano | kimi-k2.5 | Gemini(参考) |
|------|-----------|-----------|-------------|
| VTuber风格/活泼度 | ⭐⭐⭐ | ⭐⭐⭐⭐½ | ⭐⭐⭐⭐⭐ |
| 忠实度/准确性 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 术语/招牌语处理 | ⭐⭐ | ⭐⭐⭐½ | ⭐⭐⭐⭐⭐ |
| 语气还原 | ⭐⭐½ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

nano核心问题：VTuber招牌语丢失（`こんこんきーつね` → "嗨，大家好～"❌），语义错误频率高。

---

## 五、全流程定性评估（3个视频，837条逐条阅读）

对3个视频的全量翻译，逐条对照原文日语、Gemini翻译、Volc kimi-k2.5翻译，基于语义准确性、VTuber风格、专有名词处理做人工定性评估。

### 整体质量分布

| 视频 | 条数 | 持平 | Gemini更好 | Volc更好 | 两者都差 |
|------|------|------|-----------|---------|---------|
| _QOMPli80JA (对话) | 155 | ~120(77%) | ~15(10%) | ~8(5%) | ~12(8%) |
| czIBPN1eCbU (美食) | 448 | ~380(85%) | ~35(8%) | ~15(3%) | ~18(4%) |
| k8CCqKYx2Pk (ASR差) | 234 | ~180(77%) | ~35(15%) | ~8(3%) | ~11(5%) |
| **总计** | **837** | **~680(81%)** | **~85(10%)** | **~31(4%)** | **~41(5%)** |

### Gemini更好的模式（~85条，占10%）

**1. ASR乱码纠错（占差距的~60%）**

Gemini能"看穿"ASR乱码推断正确含义，kimi/deepseek直译乱码：

```
k8CCqKYx2Pk #14: "アバフライサー"
  Gemini: 马力欧卡丁车！ ← 看穿乱码
  Kimi:   ABoFlieser！ ← 直译乱码

czIBPN1eCbU #5: "えーしょうゆです"（实际是自我介绍）
  Gemini: 我是白上吹雪。 ← 识别ASR错误
  Kimi:   诶——是酱油哟～ ← 直译"しょうゆ=酱油"
```

**2. VTuber专有知识（占差距的~25%）**

```
k8CCqKYx2Pk #2: "snacksどもねーす"
  Gemini: すこん部各位好呀—— ← 识别粉丝团名
  Kimi:   snacks的大家好呀—— ← 直译

k8CCqKYx2Pk #101: "ボギーゾーだ"
  Gemini: 是耀西耶！ ← 识别马里奥角色名
  Kimi:   是博基哦～ ← 音译
```

**3. 品牌名/文化引用（占差距的~15%）**

```
czIBPN1eCbU #62-71: "スミレ"（拉面品牌名）
  Gemini: Sumire ← 全程正确保留品牌名
  Kimi:   紫罗兰 ← 连续10条错译（后半段#330+自行修正）
```

### Volc kimi-k2.5更好的模式（~31条，占4%）

**1. VTuber风格更活泼**
```
_QOMPli80JA #38: "痛い"
  Gemini: 好疼。      ← 平实
  Kimi:   痛痛～      ← 更可爱

_QOMPli80JA #123: "もうモコだよモコですよ"
  Gemini: 就是说呀，真是的。       ← 丢失拟声意象
  Kimi:   已经气鼓鼓了哟～气鼓鼓的！ ← 生动保留
```

**2. 数字/事实更忠实**
```
czIBPN1eCbU #197: "10分で来た来た来た来た！"
  Gemini: 15分啦！        ← 错误修改原文数字
  Kimi:   10分钟到啦到啦！ ← 保留原文
```

---

## 六、ASR质量评估

### 6.1 大规模参数评测（350个VTuber视频）

从Hololive旗下49203个视频中筛选350个有YouTube人工标注日语字幕的视频，用CER(字符错误率)评估。

**CER分布**：
- 平均CER约50-60%，但有误导性——45%样本是歌曲/音乐视频（ASR无法处理）
- 剩余正常样本CER主要来自汉字vs假名写法差异、标点差异
- 实际对比ASR和GT文本，内容基本正确

**参数效果排名**（167个正常样本）：

| 排名 | 参数 | 平均CER | 相对baseline |
|------|------|---------|-------------|
| 1 | copt=false + HST=2 | 49.2% | +2.8% |
| 2 | HST=2 | 49.9% | +1.4% |
| 3 | copt=false | 50.5% | +0.4% |
| 11 | baseline | 50.7% | 0% |
| 14 | repetition_penalty=1.2 | 51.3% | -1.2% |

关键结论：`condition_on_previous_text=false`和`hallucination_silence_threshold=2`轻微改善；`repetition_penalty>1.0`反而有害；`initial_prompt`无益处。参数调优总收益有限（~1-3% CER）。

### 6.2 模型对比（large-v3 vs large-v3-turbo）

| 指标 | large-v3 beam7 | turbo beam5 |
|------|---------------|-------------|
| 速度 | 基准 | 快**3-4倍** |
| 质量 | 基准 | 基本相当 |
| 核心乱码 | 无改善 | 无改善 |
| 断句 | 正常 | 更碎（笑声/语气词独立成段） |

### 6.3 beam_size对比

beam=5是性价比最优（与beam=7差异微小，速度提升约10%）。

### 6.4 当前生产配置

```yaml
asr:
  model: "large-v3"
  beam_size: 7                    # 建议改为5
  condition_on_previous_text: false
  temperature: [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
  no_speech_threshold: 0.6
  initial_prompt: ""
  repetition_penalty: 1.0
  hallucination_silence_threshold: 2
```

### 6.5 已知问题

- **temperature=0.0 + condition_on_previous_text=false**：会产生重复字符幻觉，需用温度回退列表
- **ASR乱码根因**：Whisper对快语速日语口语（尤其有BGM时）的架构层面限制，换模型/调参数无法突破

---

## 七、核心瓶颈分析

**整个pipeline的最大瓶颈是ASR质量，不是翻译模型。**

基于837条全量定性评估的量化数据：
- **81%的翻译持平**——绝大多数观众不会感知 kimi 与 Gemini 的差异
- Gemini更好的10%中，**60%源于ASR纠错**——这是模型知识层面的差距，thinking模式也无法弥补
- 对于ASR质量正常的视频（约80-90%），Volc翻译效果完全可用于生产
- ASR质量差的视频是真正的瓶颈，改善方向应聚焦于ASR后处理

---

## 八、推荐方案

### 各环节推荐模型

| 环节 | 推荐模型 | 来源 | 理由 |
|------|---------|------|------|
| **翻译(translate)** | gemini-3-flash | Google API | 质量最优，ASR纠错能力强 |
| **优化(optimize)** | **kimi-k2.5** | **火山引擎 Coding Plan** | **已集成**。同语言optimize阶段有良好ASR纠错能力 |
| **断句(split)** | gpt-4o-mini | 中转站 | 测试国产模型效果不佳（过度断句），保持不变 |
| **场景识别** | gpt-4o-mini | 中转站 | 对模型要求低 |

> **关于translate是否替换为国产模型**：从837条定性评估看，81%持平、10%Gemini更好（主要是ASR纠错）。如果视频ASR质量普遍正常，可以考虑替换以节省成本。需进一步全流程测试确认。

### Split 阶段国产模型测试

用 _QOMPli80JA 的 raw ASR 文本，对比 gpt-4o-mini / volc-kimi-k2.5 / volc-deepseek-v3.2 的断句效果：

| 模型 | 断句数(前100段raw) | 问题 |
|------|-------------------|------|
| gpt-4o-mini (生产) | 72句 | 语义完整，断句合理 |
| deepseek-v3.2 | 79句(+10%) | 过度断句：在条件句中间(～ば后)、宾格助词(を后)错误断开 |
| kimi-k2.5 | 35句/50段(更碎) | 更严重的过度断句，且速度慢(48s) |

```
#1 "今回何回私たちが起こせば気が済むんですか？"（完整条件句）
  gpt-4o-mini: 保持完整 ✅
  deepseek:    拆成 "起こせば" / "気が済むんですか？" ❌ (条件句中间断开)

#7 "もうでは早速ロボ子先輩をびっくりさせちゃいましょーう"
  gpt-4o-mini: 保持完整 ✅  
  deepseek:    拆成 "ロボ子先輩を" / "びっくりさせ..." ❌ (宾语を后断开)
```

**结论：国产模型对日语语法结构（条件句、助词）的断句判断不准确，split 继续使用 gpt-4o-mini。**

### Optimize 阶段国产模型测试

kimi-k2.5 在同语言 optimize 阶段展现了良好的 ASR 纠错能力：

```
#4: "ちらかみむむきでーす" → "白上フブキです"     ← 识别出自我介绍！
#28: "キュウリさん来ましたよ" → "フブキさん来ましたよ" ← 修正人名ASR错误
#27: "ロコ先輩きたー！" → "ロボ先輩きたー！"         ← 修正人名
#3:  "こんこんきーっすねー" → "こんこんきーつねー"     ← 修正招牌语发音
```

这是一个重要发现：**虽然kimi在translate阶段（跨语言）无法纠正ASR乱码，但在optimize阶段（同语言日语→日语）可以修正许多ASR错误。** optimize用国产模型既节省Gemini额度，又能在翻译前先修正一部分ASR问题。

**已集成到 `config/default.yaml`**：optimize 使用 volc-kimi-k2.5，BS=100，thread=3。

### Optimize 对 Translate 的影响验证

初始观察到"optimize纠正ASR后translate反而翻错"的现象，经控制实验排查，确认为**误判**：

- **根因**：diskcache使旧版翻译结果来自缓存；新版optimize改变输入→cache miss→temperature=1.0下产生不同随机结果
- **控制实验**：对问题条目禁用缓存、固定输入做3轮batch翻译
  - `白上フブキですー`(#4)：3轮全部正确 "我是白上吹雪～" ✅
  - `フブキさん来ましたよ`(#28)：3轮全部正确 "吹雪来啦～" ✅（且比Gemini翻的"突然冒出来了"更准确）
  - `こんこんきーつねー`(#3)：3轮3种不同结果——但单独测5次也产生5种结果，这是招牌语翻译的**固有不稳定性**，与optimize无关

**结论：optimize没有负面影响。kimi-k2.5在optimize阶段的ASR纠错对后续翻译是正面贡献。**

### Translate 替换为国产模型的 Trade-off 分析

基于30条控制实验（kimi optimize后，Gemini translate vs kimi translate）：

| 分类 | 比例 | 说明 |
|------|------|------|
| 完全一致 | 30% | 翻译完全相同 |
| 持平（仅措辞差异） | 47% | "诶"vs"咦"、"～"vs"——"级别 |
| Gemini略好 | 20% | 语境补充更完整（如"好像不在家呀"vs"不在呢"） |
| Kimi更好 | 3% | optimize纠正ASR后kimi翻译更准确 |

**成本差异**：当前配置下Gemini translate月消耗仅~160次调用（BS=100，20个视频）≈ ¥1.2/月。切换到kimi translate只能再省¥1.2/月。

**结论：保持 kimi optimize + Gemini translate 的混合配置。** 理由：
1. Gemini translate成本已极低（optimize已省了一半调用量）
2. Gemini在复杂ASR乱码上的纠错能力无可替代（如"アバフライサー"→马里奥卡丁车）
3. 切换到全国产translate质量降幅小但收益也小，不值得trade

**切换到全国产的适用场景**：Gemini API额度不够 / 需要完全脱离Google依赖。此时大部分观众感知不到差异（只有ASR差的视频会退化）。

### 当前生产配置

```yaml
# translate: Gemini（质量最优，保持不变）
translator.llm.model: gemini-3-flash-preview
translator.llm.enable_reflect: true
translator.llm.batch_size: 100
translator.llm.thread_num: 10

# optimize: 火山引擎 kimi-k2.5（已替换，节省Gemini额度）
translator.llm.optimize.model: kimi-k2.5
translator.llm.optimize.base_url: ark.cn-beijing.volces.com/api/coding/v3
translator.llm.optimize.batch_size: 100
translator.llm.optimize.thread_num: 3

# split: gpt-4o-mini（不变）
asr.split.model: gpt-4o-mini
asr.split.base_url: api.videocaptioner.cn
```

技术细节：`call_llm` 中已内置火山引擎 `thinking=disabled` 自动注入，无需额外配置。

Few-shot prompt文件：`scripts/translation_benchmark_prompts/custom_translate_fubuki_zh_fewshot_plus.md`

### 成本对比

| 方案 | 月费 | 翻译质量 | 月产能(~200条/视频) |
|------|------|---------|-------------------|
| Gemini-3-flash | ~¥150/20k次 | ⭐⭐⭐⭐⭐ | 取决于额度 |
| **方舟 Coding Plan (Pro)** | **~¥?/月** | **⭐⭐⭐⭐** | **~90k次请求** |
| DashScope kimi-k2.5 | ¥7.9/月 | ⭐⭐⭐⭐ | ~18k次请求(Lite) |
| 中转站 nano | 按量付费 | ⭐⭐⭐ | 取决于余额 |

### 混合方案可行性

用kimi处理大部分内容、Gemini只处理ASR乱码段落，可将Gemini调用量降至~10%同时维持接近全Gemini的效果。

检测"ASR乱码段落"的可能方案：
- 基于Whisper输出的置信度（avg_logprob）标记低置信度段
- 基于文本特征（拉丁字符、重复字符、罕见假名组合）
- 翻译后检测（输出中的音译乱码、长度异常）

当前判断：可行但需单独开发。短期内直接用volc-kimi-k2.5全量翻译最简单。

---

## 九、后续改进方向

| 方向 | 优先级 | 状态 | 说明 |
|------|--------|------|------|
| **optimize集成** | 高 | ✅ 已完成 | volc-kimi-k2.5 已集成到 config，`call_llm` 已内置thinking自动关闭 |
| **translate替换测试** | 高 | 已测 | kimi opt+kimi trans 全流程已测，与纯Gemini对比见下方。当前保持Gemini translate |
| **ASR后处理改进** | 高 | 进行中 | 由另一人同步探究 |
| **翻译后异常检测** | 中 | 待做 | 自动检测乱码/幻觉/长度异常 |
| **混合方案原型** | 中 | 待做 | ASR低置信度段落检测 + kimi+Gemini混合调用 |
| **并发控制测试** | 低 | 待做 | thread_num=5稳定性（当前thread=3无问题） |

---

## 十、API套餐信息

| 套餐 | 提供商 | Base URL | 额度 | 主要用途 |
|------|--------|----------|------|---------|
| 方舟 Coding Plan (Pro) | 火山引擎 | `https://ark.cn-beijing.volces.com/api/coding/v3` | ~90k次/月 | **主力翻译** |
| DashScope Coding Plan | 阿里云 | `https://coding.dashscope.aliyuncs.com/v1` | ~18k次/月(Lite) | 备用 |
| videocaptioner中转站 | 第三方 | `https://api.videocaptioner.cn` | 按量 | split/scene_identify |

**注意**：
- 火山引擎必须用 `/api/coding/v3`（非 `/api/v3`，后者会产生额外费用）
- 火山引擎 thinking 自动关闭已内置到 `call_llm`（`vat/llm/client.py`），无需手动配置
- 环境变量 `VAT_VOLC_APIKEY` 需要设置火山引擎 API Key
- 所有API密钥通过环境变量管理，不写入代码/配置的明文中

---

## 十一、评测工具说明

### 翻译评测

```bash
# 运行单个模型测试
python scripts/translation_benchmark.py --video-id _QOMPli80JA --model volc-kimi-k2.5 \
    --sample 0 --prompt-lang zh --batch-size 100 \
    --custom-translate-prompt scripts/translation_benchmark_prompts/custom_translate_fubuki_zh_fewshot_plus.md

# 生成对比表
python scripts/translation_benchmark.py --video-id _QOMPli80JA --compare

# 可用模型
python scripts/translation_benchmark.py --list-models
```

支持的参数：`--no-reflect`(standard模式)、`--temperature`、`--skip-optimize`、`--tag`(区分实验)

### ASR参数评测

```bash
python scripts/asr_evaluation/run_param_experiment.py --phase full --gpus 4
python scripts/asr_evaluation/evaluate_params.py
```

### 文件结构

```
scripts/
├── translation_benchmark.py                           # 翻译评测脚本
├── translation_benchmark_prompts/
│   ├── optimize_system_zh.md                          # 中文optimize系统提示词
│   ├── translate_reflect_zh.md                        # 中文translate系统提示词
│   ├── custom_translate_fubuki_zh_fewshot_plus.md     # 12条few-shot（最优）
│   └── ...
├── translation_benchmark_results/                     # 所有翻译实验的原始数据
│   ├── _QOMPli80JA/                                   # 主测试视频（22+组配置）
│   ├── k8CCqKYx2Pk/                                   # ASR差案例
│   ├── czIBPN1eCbU/                                   # 美食实况
│   └── ...（共12个视频目录）
└── asr_evaluation/                                    # ASR参数评测脚本

docs/
├── TRANSLATION_AND_ASR_EVALUATION.md                  # 本文档
└── ASR_EVALUATION_REPORT.md                           # ASR 350视频参数评测详细数据
```

---

*最后更新: 2026-03-02*
