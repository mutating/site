# @mutating/ui

Общая визуальная библиотека The Mutating Company. Эталон — статический сайт
`site/themes/mutating`. Хэдер статического сайта уже подключён к библиотеке;
остальные компоненты сайта и платформа мигрируют отдельно. Графики в пакет не входят.

## Состав и стек

- Обычный CSS: палитра светлой и тёмной тем, локальные шрифты, фон страницы,
  поверхности, кнопки, логотип и адаптивный хэдер.
- TypeScript без зависимости от React: управление темой, анимации глаза и
  названия, переключатель языков и мобильное меню.
- React-компоненты и Hugo partials с одинаковым контрактом классов `tmc-*`.
- Vite собирает ES-модули, TypeScript — декларации типов. CSS, шрифты и маска
  глаза сохраняются отдельными файлами с относительными ссылками.

```text
src/styles/       Общие стили; index.css подключает весь набор
src/assets/       Локальные шрифты, их лицензии и маска глаза
src/theme.ts      Тема, хранение выбора и синхронизация вкладок
src/brand.ts      Поведение логотипа
src/header.ts     Поведение переключателей и мобильного меню
src/react/        React-адаптеры
hugo/partials/    Hugo-адаптеры
demo/            Примеры HTML и React
dist/            Результат сборки, не хранится в Git
```

## Разработка

Из корня репозитория:

```sh
npm ci
npm run dev:ui
npm run build:ui
npx playwright install chromium firefox
npm run test:ui
```

Демо: <http://127.0.0.1:18120/> и <http://127.0.0.1:18120/react.html>.
Конфигурация главной: <http://127.0.0.1:18120/home.html>; русский вариант —
<http://127.0.0.1:18120/home.html?language=ru>.
Они используют исходники библиотеки и обновляются через Vite. Для тестов также
нужен Hugo Extended `0.164.0`, как для основного сайта. Тесты собирают пакет,
проверяют импорт без браузера, локальные ресурсы и рендеринг шаблонов Hugo,
затем запускают HTML, React и Hugo в Chromium на десктопе и мобильном экране.
Отдельный набор Firefox проверяет совпадение с исходным хэдером по пикселям.

## Стили

Потребитель явно подключает нужные стили:

```ts
import "@mutating/ui/styles.css";
// Или отдельные слои:
// import "@mutating/ui/tokens.css";
// import "@mutating/ui/fonts.css";
// import "@mutating/ui/styles/buttons.css";
```

Палитра использует имена из сайта: `--bg`, `--surface`, `--surface-raised`,
`--ink`, `--ink-muted`, `--hot`, `--hot-bright`, `--hot-soft`, `--hot-pale`,
`--line`, `--inverse`, `--inverse-ink`, `--shadow`. Тема задаётся на корневом
`html` через `data-theme="light"` или `data-theme="dark"`. До запуска JavaScript
CSS учитывает системную тему. Ширину контента, скругления поверхностей и кнопок
и скорость переходов можно менять через `--tmc-shell-width`, `--tmc-radius-control`,
`--tmc-radius-panel`, `--tmc-motion-fast`.

`tmc-page` на `body` включает фон и типографику страницы. `tmc-shell` задаёт
ширину контента; `tmc-surface` и `tmc-surface--raised` оформляют поверхности.
Стили компонентов ограничены классами `tmc-*`; универсального сброса для
всего документа нет. Импорт JS сам не подключает CSS и не меняет DOM.

Кнопки: `tmc-button` + `tmc-button--primary`, `--secondary` или `--dark`;
дополнительно `tmc-button--compact`. Классы подходят для `button` и `a`.
Для отключения действия используйте нативный `button disabled`; `aria-disabled`
у ссылки меняет её оформление, а обработку перехода определяет потребитель.

## React

После `npm run build:ui` пакет доступен по имени через npm workspaces.
При будущем подключении потребитель должен объявить зависимость
`"@mutating/ui": "0.1.0"` в своём `package.json`.

```tsx
import "@mutating/ui/styles.css";
import { Button, Header, ThemeProvider } from "@mutating/ui/react";

const items = [
  { id: "overview", label: "Обзор", href: "/", active: true },
  { id: "organizations", label: "Организации", href: "/organizations/" },
];

export function Example() {
  return (
    <ThemeProvider>
      <Header
        brand={{ href: "/", label: "На главную" }}
        items={items}
        navigationLabel="Навигация"
        themeLabel="Переключить тему"
        menuLabel="Открыть меню"
        menuText="Меню"
      />
      <main className="tmc-shell">
        <Button onClick={() => console.log("action")}>Продолжить</Button>
        <Button variant="secondary" size="compact">Отмена</Button>
      </main>
    </ThemeProvider>
  );
}
```

`ThemeProvider` управляет одним контроллером, включая очистку в React StrictMode.
`useTheme()` возвращает `{ theme, controller }`; пока эффект провайдера не
выполнен, `controller` равен `null`. Контроллер предоставляет `setTheme`,
`toggle` и `useSystemTheme`. `ThemeToggle` требует провайдер.

## Настройка хэдера

`HeaderConfig` — общий контракт React и Hugo. Один массив `items` задаёт оба
меню. Мобильную копию списка собирать не нужно; изменения массива обновляют
оба представления, включая порядок, иконки и активный пункт.

| Поле | Значение |
| --- | --- |
| `items` | Массив `{ id, label, href, icon?, active?, target?, rel? }` |
| `brand` | `{ href, label?, animation? }`; по умолчанию ссылка `/` |
| `language` | `{ current, options, label, groupLabel, storageKey?, supportedCodes? }`; без поля переключатель скрыт |
| `adjacentLink` | `{ href, label }`; необязательная отдельная ссылка справа |
| `showThemeToggle` | По умолчанию `true`; `false` скрывает переключатель темы |
| `themeLabel` | Доступное имя переключателя темы |
| `navigationLabel`, `mobileNavigationLabel` | Доступные имена навигации |
| `menuLabel`, `menuText` | Доступное имя и текст кнопки мобильного меню |
| `inheritTypography` | Наследовать цвет, шрифт и межстрочный интервал окружения; включено в конфигурации главной |

`id` — стабильный идентификатор пункта. `icon: "github"` добавляет исходную
иконку сайта. `active: true` выставляет `aria-current="page"`. Для `target="_blank"`
без явного `rel` добавляется `noopener noreferrer`. Доступность пунктов, тексты,
переводы и URL определяет потребитель, передавая итоговый массив.
Явно переданные пустые строки сохраняются в обоих адаптерах: например,
`menuText: ""` оставляет только иконку меню, `rel: ""` отключает библиотечное
значение по умолчанию, а `language.storageKey: ""` выбирает пустой ключ storage.
Для значения по умолчанию нужно опустить соответствующее поле.

`language.options` — массив `{ code, href, label? }`. Header сам размещает
переключатель в десктопной шапке и в мобильном меню. Отдельный компонент
`LanguageSwitcher` также доступен для самостоятельного использования.

React дополнительно принимает `renderLink(item, attributes, children)` —
через него можно подключить роутер приложения, сохранив оформление и содержимое
ссылок. `brand.animation` и отдельный `Brand.animation` принимают `expandWordmark`,
`scrollTone`, `blinkIntervalMs` (0 отключает периодическое моргание) и
`activation: "navigate" | "reload-home"`. По умолчанию логотип — обычная ссылка.
Режим `reload-home` использует исходный `div[role=link][tabindex=0]`: это действие
перезагрузки без нативного открытия вкладки и перетаскивания URL. Оно сохраняет
системный цвет текста в высокой контрастности Chromium. Закрытие глаза и
перезагрузка текущей главной работают как на исходном сайте.
Для `reload-home` пустой `href` означает текущий документ. Перезагрузка с
сохранением query и очисткой hash выполняется только при совпадении origin и
пути домашней страницы; другой origin или путь открывается обычным переходом.

### Конфигурация главной mutating.tech

Это тот же Header с обычными данными, без отдельной реализации или CSS-варианта:

```tsx
import { createMutatingHomeHeader } from "@mutating/ui";
import { Header, ThemeProvider } from "@mutating/ui/react";

const home = createMutatingHomeHeader("ru"); // "en" по умолчанию
<ThemeProvider><Header {...home} /></ThemeProvider>;
```

Фабрика задаёт исходный порядок Sources / News / Blog / Contact, переводы,
иконку GitHub, языковые ссылки, поведение логотипа и ссылку на платформу.
Можно изменить любое поле возвращённого объекта, например заменить `items`
на пункты кабинета. При сборке эти же данные записываются в
`dist/presets/en.json` и `dist/presets/ru.json`, чтобы Hugo не дублировал конфигурацию.

## HTML и Hugo

В HTML используйте классы из демо. Подключите CSS и явно смонтируйте поведение:

```js
import { createThemeController, mountHeader } from "@mutating/ui";

const theme = createThemeController();
const destroy = mountHeader(document.querySelector(".tmc-header-shell"), { theme });
// При удалении фрагмента:
destroy();
theme.destroy();
```

`mountHeader` создаёт собственный контроллер, если `theme` не передан;
в этом случае `destroy` освобождает и его. Отдельно экспортируются
`mountBrand`, `mountThemeToggle`, `mountLanguageSwitcher`, `mountMobileMenu`.
Каждая функция возвращает очистку обработчиков, наблюдателей, таймеров
и кадров анимации; очистку можно вызывать повторно. Обработчики удаляются явно,
без обязательной поддержки AbortController и signal у addEventListener.

У `mountHeader` можно настроить закрытие меню. Статический сайт сохраняет
поведение исходной реализации следующими опциями:

```js
mountHeader(header, {
  language: { closeOnSelection: false, stopEscapePropagation: false },
  themeToggle: { exposePressedState: false },
  brand: false,
  mobileMenu: false,
  themeOptions: {
    initialTheme: document.documentElement.dataset.theme === "light" ? "light" : "dark",
    syncAcrossTabs: false,
    preferenceSource: "storage",
  },
});
// После инициализации изображений и ticker:
mountBrand(header.querySelector(".tmc-brand"));
// После остальных визуализаций сайта:
mountMobileMenu(header.querySelector(".tmc-mobile-menu"), {
  closeOnEscape: false, closeOnDesktop: false, closeNestedMenus: false,
});
```

В обычном режиме библиотеки эти способы закрытия и синхронизация темы включены.
`brand: false` и `mobileMenu: false` откладывают соответствующие этапы до явного
mount; так сайт сохраняет исходный порядок инициализации при отказе браузерного
API. `themeToggle.exposePressedState: false` сохраняет исходную семантику кнопки
действия без aria-pressed. По умолчанию библиотечный переключатель сообщает
состояние через aria-pressed.
`stopEscapePropagation: false` сохраняет всплытие Escape к обработчикам документа.
Начальная тема из `html[data-theme]` сохраняет уже показанную head-скриптом тему,
если предпочтение поменялось за время загрузки отложенного JavaScript.
`language.supportedCodes` резервирует ширину по всем поддерживаемым кодам,
даже когда в `options` отсутствуют ссылки на переводы текущей страницы.

Для подключения Hugo добавьте mounts в конфигурацию сайта
(пути ниже рассчитаны относительно существующего `site/`):

```toml
[[module.mounts]]
source = "../packages/ui/hugo"
target = "layouts"

[[module.mounts]]
source = "../packages/ui/dist"
target = "static/ui"
```

Сохраните существующие mounts, если они уже заданы. Затем подключите
`{{ "ui/styles/index.css" | relURL }}` через `link rel="stylesheet"`,
а `{{ "ui/index.js" | relURL }}` — через импорт в `script type="module"`.
Копируется весь `dist`, включая JS-чанки, CSS и ресурсы. Сначала собирается
библиотека, затем Hugo. Статический сайт дополнительно монтирует `dist` в
`assets/ui`, чтобы Hugo мог включить JS в общий bundle, а CSS снабдить fingerprint.
Его `partials/header.html` задаёт локализованные данные и ссылки с текущей страницы;
разметка, стили и поведение берутся из пакета. В сайт подключаются только
`brand.css` и `header.css`: фон, базовые стили и шрифты пока остаются в теме сайта.

```go-html-template
{{ partial "tmc-ui/header.html" (dict
  "brand" (dict "href" ("/" | relLangURL) "label" "На главную")
  "navigationLabel" "Навигация"
  "themeLabel" "Переключить тему"
  "menuLabel" "Открыть меню"
  "menuText" "Меню"
  "items" (slice (dict "id" "blog" "href" ("/blog/" | relLangURL) "label" "Блог"))
  "language" (dict
    "current" "ru" "label" "Выбрать язык" "groupLabel" "Языки"
    "options" (slice (dict "code" "en" "href" "/") (dict "code" "ru" "href" "/ru/"))
  )
) }}
{{ partial "tmc-ui/button.html" (dict "text" "Продолжить" "href" "/start/") }}
```

Также доступны отдельные partials `brand.html`, `theme-toggle.html`,
`language-switcher.html`, `menu-link.html`. Параметры Header соответствуют
`HeaderConfig`, включая `language.storageKey` и `brand.animation`.
`mountHeader` читает их из созданных адаптером data-атрибутов.

React-адаптер обновляет обработчики при смене текущего языка или списка языков.
Для мобильных ссылок он использует `mountMobileMenu(menu, { delegateLinks: true })`:
делегирование сохраняет закрытие меню при
замене `renderLink`, включая ссылки роутера. Обычные ререндеры с новой inline
функцией `renderLink` не сбрасывают открытое меню. По умолчанию HTML/Hugo
используют обработчики на самих ссылках.

Для использования готовой конфигурации главной добавьте ещё один mount:

```toml
[[module.mounts]]
source = "../packages/ui/dist/presets"
target = "data/tmc_ui_home"
```

```go-html-template
{{ partial "tmc-ui/header.html" (index hugo.Data.tmc_ui_home .Site.Language.Lang) }}
```

## Тема и доступность

`createThemeController({ root?, storageKey?, initialTheme? })` предпочитает
явную тему, затем сохранённый выбор, затем системную тему. Ключ по умолчанию —
`tmc-theme`; при недоступности localStorage выбор хранится в памяти.
`useSystemTheme()` сбрасывает явный выбор. Изменения синхронизируются между
вкладками одного origin. На разных поддоменах localStorage отдельный.
`syncAcrossTabs: false` отключает синхронизацию между вкладками.
`preferenceSource: "storage"` перечитывает сохранённый выбор при каждом изменении
системной темы; удалённое или недоступное хранилище возвращает управление системе.
По умолчанию используется `"memory"`, сохраняющий ручной выбор при отказе storage.
`root` по умолчанию — `document.documentElement`; CSS темы рассчитан на `html`.
`subscribe(listener)` возвращает отписку. Изменение темы публикует DOM-событие
`tmc-theme-change`; нажатие переключателя — `tmc-theme-toggle-blink` для глаза.

Для установки темы до первого отображения можно вызвать контроллер из раннего
скрипта. Компоненты поддерживают клавиатуру и `prefers-reduced-motion`;
Escape закрывает открытое меню и возвращает фокус на его переключатель.
Лейблы, ссылки, переводы и бизнес-логика остаются у потребителя.

## Попиксельная проверка

`tests/ui/prepare-reference.mjs` использует сохранённую до миграции главную
из `tests/site/header/reference` на английском и русском, включая её фон,
шрифты и исходные функции хэдера.
Для сравнения подставляется Header из библиотеки: сначала Hugo partial,
затем настоящий React-компонент с гидратацией. Рабочие файлы сайта не меняются.

Проверяется 19 ширин от 280 до 1440 пикселей, включая обе стороны существующих
брейкпоинтов, обе темы и обе локализации. Отдельные сценарии сравнивают фокус,
открытые мобильное и языковое меню, кадры раскрытия названия, смены темы и
моргания, закрытие глаза при активации главной и шапку над фоном при прокрутке.

Время JavaScript и CSS-анимаций фиксируется. Маски и допуск несовпадающих
пикселей не используются: PNG оригинала и кандидата должны совпадать побайтно,
включая сглаживание. Проверка работает в Firefox из закреплённой версии
Playwright. Chromium используется для функциональных сценариев: его округление
полупрозрачных краёв различается даже при повторном рисовании оригинала.
В CI устанавливаются оба браузера. Ожидаемые изображения создаются заново из
оригинала в том же окружении и сохраняются в `.runtime/pixel-expected/`.

## Ресурсы и дальнейшее внедрение

CSS, алгоритмы анимаций и ресурсы скопированы из текущей темы в самостоятельный
пакет. Он не читает файлы сайта при сборке. Science Gothic и Geist распространяются
с приложенными лицензиями в `src/assets/fonts/`.

Изменения хэдера следует делать здесь. Его прежние стили, разметка и алгоритмы
удалены из темы статического сайта. После `npm run build:ui` изменения попадут
в следующий Hugo build. Остальные компоненты и платформа подключаются отдельно.
