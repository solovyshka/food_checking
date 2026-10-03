"""Keep label-relevant product identity in the mobile diary."""
from app.services.parser import CONSUMPTION_SYSTEM_PROMPT, _openai_chat, parsed_from_model_content

MOBILE_PROMPT = CONSUMPTION_SYSTEM_PROMPT + """
Для мобильного дневника название должно различать варианты продукта.
ОБЯЗАТЕЛЬНО сохраняй явно названные жирность, марку и вид приготовления.
«молоко 2.5%» → name «молоко 2.5%», а не просто «молоко».
«творог Простоквашино 5%» → name «творог Простоквашино 5%».
Не добавляй марку или жирность, если человек их не указал.
Если количество явно названо в г или мл, сохрани эту единицу, даже для жидкости.
Числа жирности записывай с точкой, например 2.5%, чтобы название было стабильным.
"""


async def parse_consumption_text(text):
    content = await _openai_chat(MOBILE_PROMPT, text)
    return parsed_from_model_content(content, consumption=True), "mobile"
