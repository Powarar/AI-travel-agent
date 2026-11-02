/**
 * Русское написание названий из каталога.
 *
 * Источник (`data/seeds/destinations.json`) хранит имя ровно в том написании, которое отдал
 * GeoNames, и контракт это требует сохранять: `ref["name"]` — написание источника, а
 * транслитерация остаётся на фронте (AGENTS.md, «Что в ref»).
 * Экран под жюри говорит по-русски, поэтому переводит строки здесь. Ключ — точное написание
 * источника, а не угадывание по буквам: не нашлось совпадения — показываем как есть.
 */
const NAME_RU: Record<string, string> = {
  Sochi: 'Сочи',
  Anapa: 'Анапа',
  Gelendzhik: 'Геленджик',
  Yalta: 'Ялта',
  Sevastopol: 'Севастополь',
  Alushta: 'Алушта',
  Sudak: 'Судак',
  Feodosiya: 'Феодосия',
  Yevpatoriya: 'Евпатория',
  Kislovodsk: 'Кисловодск',
  Pyatigorsk: 'Пятигорск',
  'Mineralnye Vody': 'Минеральные Воды',
  Belokurikha: 'Белокуриха',
  Moscow: 'Москва',
  'Saint Petersburg': 'Санкт-Петербург',
  Kaliningrad: 'Калининград',
  Kazan: 'Казань',
  'Nizhniy Novgorod': 'Нижний Новгород',
  Yekaterinburg: 'Екатеринбург',
  Vladivostok: 'Владивосток',
  'Yuzhno-Sakhalinsk': 'Южно-Сахалинск',
  'Kuril’sk': 'Курильск',
  'Yuzhno-Kurilsk': 'Южно-Курильск',
  'Petropavlovsk-Kamchatsky': 'Петропавловск-Камчатский',
  'Ulan-Ude': 'Улан-Удэ',
  Irkutsk: 'Иркутск',
  'Gorno-Altaysk': 'Горно-Алтайск',
  Makhachkala: 'Махачкала',
  Sokhumi: 'Сухум',
  Gagra: 'Гагра',
  Tbilisi: 'Тбилиси',
  Batumi: 'Батуми',
  Yerevan: 'Ереван',
  Baku: 'Баку',
  Almaty: 'Алматы',
  Bishkek: 'Бишкек',
  'Cholpon-Ata': 'Чолпон-Ата',
  Samarkand: 'Самарканд',
  Bukhara: 'Бухара',
  Tashkent: 'Ташкент',
  Minsk: 'Минск',
  Belgrade: 'Белград',
  Antalya: 'Анталья',
  Alanya: 'Аланья',
  Kemer: 'Кемер',
  Bodrum: 'Бодрум',
  Marmaris: 'Мармарис',
  Istanbul: 'Стамбул',
  Hurghada: 'Хургада',
  'Sharm el-Sheikh': 'Шарм-эль-Шейх',
  Dubai: 'Дубай',
  'Abu Dhabi': 'Абу-Даби',
  Phuket: 'Пхукет',
  Pattaya: 'Паттайя',
  Bangkok: 'Бангкок',
  'Da Nang': 'Дананг',
  'Nha Trang': 'Нячанг',
  Sanya: 'Санья',
  Male: 'Мале',
  Larnaca: 'Ларнака',
  Paphos: 'Пафос',
}

/**
 * Длинные имена первыми: `Yuzhno-Kurilsk` содержит `Kuril’sk`-подобное «Kurilsk», и короткое
 * ключевое слово съело бы середину длинного.
 */
const BY_SOURCE_SPELLING = Object.keys(NAME_RU).sort((a, b) => b.length - a.length)

const IN_TEXT = new RegExp(`\\b(${BY_SOURCE_SPELLING.map(escapeRe).join('|')})\\b`, 'g')

function escapeRe(name: string): string {
  return name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Название или целая строка с названиями → по-русски; неизвестное остаётся как было. */
export function ruNames<T extends string | null | undefined>(text: T): T {
  if (!text) return text
  const exact: string | undefined = NAME_RU[text]
  return (exact ?? text.replace(IN_TEXT, (found) => NAME_RU[found] ?? found)) as T
}
