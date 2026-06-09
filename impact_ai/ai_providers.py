from dataclasses import dataclass


@dataclass(frozen=True)
class AIProvider:
    id: str
    name: str
    family: str
    default_model: str
    model_env: str
    api_key_env: str
    base_url_env: str
    default_base_url: str
    max_input_tokens: int
    max_output_tokens: int


def provider_catalog() -> list[AIProvider]:
    """Supported mainstream global and China-market AI providers."""
    return [
        AIProvider(
            id="openai",
            name="OpenAI",
            family="global",
            default_model="gpt-4.1",
            model_env="OPENAI_MODEL",
            api_key_env="OPENAI_API_KEY",
            base_url_env="OPENAI_BASE_URL",
            default_base_url="https://api.openai.com/v1",
            max_input_tokens=1_000_000,
            max_output_tokens=32_768,
        ),
        AIProvider(
            id="anthropic",
            name="Anthropic",
            family="global",
            default_model="claude-3-7-sonnet-latest",
            model_env="ANTHROPIC_MODEL",
            api_key_env="ANTHROPIC_API_KEY",
            base_url_env="ANTHROPIC_BASE_URL",
            default_base_url="https://api.anthropic.com/v1",
            max_input_tokens=200_000,
            max_output_tokens=16_384,
        ),
        AIProvider(
            id="gemini",
            name="Google Gemini",
            family="global",
            default_model="gemini-2.5-pro",
            model_env="GEMINI_MODEL",
            api_key_env="GEMINI_API_KEY",
            base_url_env="GEMINI_BASE_URL",
            default_base_url="https://generativelanguage.googleapis.com/v1beta",
            max_input_tokens=1_000_000,
            max_output_tokens=65_536,
        ),
        AIProvider(
            id="deepseek",
            name="DeepSeek",
            family="china",
            default_model="deepseek-chat",
            model_env="DEEPSEEK_MODEL",
            api_key_env="DEEPSEEK_API_KEY",
            base_url_env="DEEPSEEK_BASE_URL",
            default_base_url="https://api.deepseek.com/v1",
            max_input_tokens=64_000,
            max_output_tokens=8_192,
        ),
        AIProvider(
            id="qwen",
            name="Alibaba Qwen",
            family="china",
            default_model="qwen-max",
            model_env="QWEN_MODEL",
            api_key_env="QWEN_API_KEY",
            base_url_env="QWEN_BASE_URL",
            default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        ),
        AIProvider(
            id="zhipu",
            name="Zhipu GLM",
            family="china",
            default_model="glm-4-plus",
            model_env="ZHIPU_MODEL",
            api_key_env="ZHIPU_API_KEY",
            base_url_env="ZHIPU_BASE_URL",
            default_base_url="https://open.bigmodel.cn/api/paas/v4",
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        ),
        AIProvider(
            id="moonshot",
            name="Moonshot Kimi",
            family="china",
            default_model="moonshot-v1-128k",
            model_env="MOONSHOT_MODEL",
            api_key_env="MOONSHOT_API_KEY",
            base_url_env="MOONSHOT_BASE_URL",
            default_base_url="https://api.moonshot.cn/v1",
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        ),
        AIProvider(
            id="doubao",
            name="ByteDance Doubao",
            family="china",
            default_model="doubao-pro-128k",
            model_env="DOUBAO_MODEL",
            api_key_env="DOUBAO_API_KEY",
            base_url_env="DOUBAO_BASE_URL",
            default_base_url="https://ark.cn-beijing.volces.com/api/v3",
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        ),
        AIProvider(
            id="hunyuan",
            name="Tencent Hunyuan",
            family="china",
            default_model="hunyuan-turbos-latest",
            model_env="HUNYUAN_MODEL",
            api_key_env="HUNYUAN_API_KEY",
            base_url_env="HUNYUAN_BASE_URL",
            default_base_url="https://api.hunyuan.cloud.tencent.com/v1",
            max_input_tokens=256_000,
            max_output_tokens=8_192,
        ),
        AIProvider(
            id="ernie",
            name="Baidu ERNIE",
            family="china",
            default_model="ernie-4.0-turbo-128k",
            model_env="ERNIE_MODEL",
            api_key_env="ERNIE_API_KEY",
            base_url_env="ERNIE_BASE_URL",
            default_base_url="https://qianfan.baidubce.com/v2",
            max_input_tokens=128_000,
            max_output_tokens=8_192,
        ),
    ]
