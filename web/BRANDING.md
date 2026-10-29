# Визуальная основа «О! Куда»

Интерфейс опирается на [описание ребрендинга Островка](https://blog.ostrovok.ru/o-rebrending/):
основной зелёный «Мохито», дополнительный синий «Ультрамарин» и мотив «О!».
Формальный брендбук с правилами для стороннего продукта в репозитории отсутствует.

- `#ACEF81` — зелёный фон, взят из [изображения Островка](https://blog.ostrovok.ru/wp-content/uploads/2024/04/Frame-109.png) с новым логотипом.
- `#0E41D2` — синий цвет [векторного логотипа 2024 года](https://commons.wikimedia.org/wiki/File:Ostrovok_logo_2024.svg). Этот файл скопирован в `public/icons/ostrovok-logo.svg`; Commons указывает в качестве источника ostrovok.ru.
- Токены цветов, радиусов и теней находятся в `src/shared/styles/_variables.scss`.
- Переиспользуемые элементы в `src/shared/ui`: `BrandLockup`, `Button`, `Card`, `Container`, `PromptChip`, `TextareaField`. Их стили изолированы CSS Modules.

`src/app/App.tsx` переключает `HomePage` и `PlanningPage`. Экран подбора — две колонки: `widgets/ChatPanel` (диалог, журнал действий агента, вопрос чипами, `features/send-message/Composer`) и `widgets/TripBoard` (`PhaseStepper`, `TripSummary` с `BudgetMeter`, `TripAlerts`, `PlanDiff`, `FinalistCards`, `TripTimeline`, `PriceBreakdown`, `features/checkout`). Состояние сессии — `features/trip-session/useTripSession`. Анимации: общие классы в `shared/styles/_motion.scss`; CSS Modules локализуют имена keyframes, поэтому модуль с анимацией подключает `shared/styles/keyframes` сам. Все анимации выключаются при `prefers-reduced-motion`. Иконки — `shared/ui/Icon` (линейные 24×24, без пакетов). По умолчанию данные приходят от backend через `/api`; для офлайн-демо записанных сценариев задайте `VITE_API_MODE=mock`.

Шрифт фирменного логотипа сохранён в SVG. Для текста интерфейса используется системный округлый гротеск: фирменные файлы шрифта и правила его лицензирования не предоставлены.
