# Technical SEO tasks

Backlog сформирован по итогам 20 независимых SEO-аудитов и сгруппирован по приоритету. `[x]` означает, что задача реализована и проверена в репозитории; live-пункты отмечаются только после применения конфигурации на production.

## P0 — критические исправления

### Канонический хост, HTTPS и серверная конфигурация

- [x] Выпустить TLS-сертификат, покрывающий `mutating.tech` и `www.mutating.tech`.
- [x] Настроить однопереходный `301` или `308` с `http://mutating.tech/*` на `https://mutating.tech/*` с сохранением path и query string.
- [x] Настроить однопереходный `301` или `308` с `http://www.mutating.tech/*` на `https://mutating.tech/*` с сохранением path и query string.
- [x] Настроить однопереходный `301` или `308` с `https://www.mutating.tech/*` на `https://mutating.tech/*` с сохранением path и query string.
- [x] Разделить в nginx canonical content-host и redirect-hosts.
- [x] Сделать TLS-конфигурацию Ansible идемпотентной: повторный запуск playbook не должен удалять рабочий HTTPS server block.
- [x] Перевести Certbot на предсказуемую схему `certonly --webroot`, а итоговые TLS server blocks хранить в Ansible.
- [x] После исправления всех HTTPS-хостов добавить HSTS; `preload` включать только отдельным осознанным решением.
- [x] Добавить live-проверку TLS SAN, redirect chain и canonical host после каждого деплоя.

### Production build и canonical origin

- [x] Вынести production `baseURL = "https://mutating.tech/"` в отдельный Hugo production-конфиг.
- [x] Собирать сайт в Ansible с `--environment production`, production base URL, `--cleanDestinationDir` и `--panicOnWarning`.
- [x] Использовать в CI и Ansible одну эквивалентную команду production-сборки.
- [x] Валидировать точный production origin: HTTPS, host `mutating.tech`, без path/query/fragment, с завершающим `/`.
- [x] Запрещать деплой, если в HTML, XML, canonical, hreflang или Open Graph найден `localhost`, `127.0.0.1`, тестовый домен или `http://`.
- [x] Пиновать используемую версию Hugo во всех путях сборки.
- [x] Установить и проверять все runtime-зависимости деплоя, включая `rsync`.
- [x] Блокировать production deploy до успешного завершения тестов и SEO-contract suite.
- [x] После rsync выполнять smoke-тест главной, `robots.txt`, sitemap, canonical, redirect-hosts и настоящего `404`.

### Сломанные и технические индексируемые страницы

- [x] Убрать `/author-posts/` и `/ru/author-posts/` из HTML output, sitemap и RSS либо превратить их в осмысленный общий архив.
- [x] Если корневой `author-posts` удаляется, настроить постоянный редирект на `/authors/` только при наличии внешних ссылок или уже накопленных сигналов.
- [x] Добавить guard в шаблон `author-posts`, запрещающий рендер страницы без существующего автора.
- [x] Добавить regression-тест, запрещающий пустые заголовки вида `Signals from .` и `Материалы автора .`.
- [x] Поставить `noindex` на `/muta-tentacles.html` и `/tokamak-reactor.html`, если они остаются техническими iframe-документами.
- [x] Исключить технические iframe-документы из sitemap и других discovery-механизмов.

### Критические Core Web Vitals

- [ ] Удалить безусловный idle-prewarm iframe Tentacles.
- [ ] Загружать Tentacles только при приближении секции к viewport или после явного взаимодействия.
- [ ] Сгенерировать lossy AVIF/WebP-варианты hero-изображения примерно в ширинах 480/768/1000/1300/1800 px.
- [ ] Исправить `srcset` и `sizes` hero так, чтобы Retina desktop не получал избыточный lossless-файл 2508 px.
- [ ] Сделать responsive AVIF/WebP-варианты портретов авторов с width descriptors и корректным `sizes`.
- [ ] Добавить портретам авторов явные `width` и `height`.
- [ ] Оставить LCP-портрет профиля eager и добавить ему `fetchpriority="high"`.
- [ ] Загружать карточки авторов вне первого viewport лениво.
- [ ] Создать responsive-варианты трёх pillar-изображений и thesis-арта.
- [ ] Повторно измерить mobile/desktop LCP, INP, CLS и TBT после оптимизации критических media.

## P1 — фундамент технического SEO

### Sitemap, robots и индексируемость

- [x] Создать собственный шаблон `robots.txt` с `Sitemap: https://mutating.tech/sitemap.xml`.
- [x] Проверять, что множество URL в sitemap строго равно множеству разрешённых индексируемых canonical HTML-страниц.
- [x] Исключить из sitemap aliases, redirects, `noindex`, технические документы и служебные section roots.
- [x] Проверять, что каждый `<loc>` отвечает `200` и имеет self-canonical.
- [x] Проверять отсутствие дубликатов `<loc>` во всех sitemap.
- [ ] Добавить локализованную пользовательскую `404.html`, сохранив реальный HTTP-статус `404`.
- [ ] Рассмотреть image sitemap или image extensions после появления содержательных обложек и иллюстраций.

### Дубли URL и aliases

- [x] Включить отключение Hugo pagination aliases `/page/1/`.
- [x] Отключить стандартный default-language alias `/en/`, если он не нужен.
- [x] Заменить необходимые legacy aliases с HTTP 200 и meta-refresh на серверные `301`/`308`.
- [x] Нормализовать `/index.html` и вложенные `*/index.html` постоянными редиректами на чистые URL.
- [x] Нормализовать URL с повторными слэшами.
- [ ] Проверять, что внутренние ссылки никогда не ведут на redirect или alias URL.
- [ ] Вести manifest публичных URL и требовать явный постоянный редирект при удалении или смене slug.

### Пагинация и сортировка

- [x] Инициализировать paginator до рендера `<head>`.
- [x] Выдавать self-canonical для каждой страницы пагинации.
- [x] Выдавать page-aware `og:url` для каждой страницы пагинации.
- [x] Сопоставлять EN/RU hreflang с тем же номером страницы пагинации.
- [x] Сохранять номер страницы при переключении языка.
- [x] Добавлять `Page N` / `Страница N` в title и description страниц 2+.
- [x] Добавить growth-fixture с количеством публикаций, достаточным минимум для трёх страниц пагинации.
- [x] Проверить пагинацию блога, тегов и авторских архивов на страницах 2–3.
- [ ] Сделать порядок материалов со совпадающими датами детерминированным с помощью secondary sort key.
- [ ] При большом количестве страниц заменить полный список paginator-ссылок на компактное окно с первой и последней страницей.

### Международное SEO

- [ ] Не публиковать индексируемый RU URL, пока для страницы нет настоящего русского перевода.
- [ ] Если временный RU fallback нужен пользователям, ставить на него `noindex,follow`, canonical на EN и исключать из sitemap/hreflang.
- [ ] Добавить CI-проверку наличия RU-перевода для каждой индексируемой EN content page.
- [x] Проверять взаимность каждой пары EN/RU hreflang на всём production-корпусе.
- [x] Проверять существование и каноничность всех hreflang targets.
- [ ] Проверять соответствие URL и `<html lang>` фактическому языку содержимого.
- [x] Унифицировать стратегию hreflang-кодов в HTML и sitemap: `en`/`ru` либо `en-US`/`ru-RU`.
- [x] Добавить `x-default` в sitemap, если sitemap продолжит содержать hreflang alternates.
- [ ] Убрать автоматический `location.replace()` по языку браузера из критического пути.
- [ ] Заменить автоматический языковой redirect на ненавязчивое предложение переключить язык либо redirect только по сохранённому явному выбору.
- [ ] Убрать `?tmc-language-choice=` из crawlable `href` языкового переключателя.
- [ ] Проверять, что языковой переключатель ведёт на чистый канонический URL.

### Title и meta description

- [ ] Ввести отдельные front matter fields `seoTitle` и `seoDescription`.
- [ ] Не использовать description главной как универсальный fallback для внутренних страниц.
- [ ] Добавить локализованные fallback-шаблоны по типу страницы.
- [ ] Требовать уникальный description для каждой индексируемой статьи, section, профиля и индексируемого терма.
- [ ] Завести локализованный реестр тегов с display title, SEO title и description.
- [ ] Исправить автоматические названия тегов вида `Llm`, `Mutation-Testing` и `Equivalent-Mutants`.
- [ ] Сделать брендовый suffix type-aware и достаточно коротким для длинных RU titles.
- [ ] Убрать лишнюю точку перед брендовым разделителем у blog index.
- [x] Добавить page-aware metadata для пагинации.
- [ ] Проверять уникальность title внутри каждого языка.
- [ ] Проверять отсутствие английского description на русских URL.
- [ ] Добавить ratchet-проверки длины и качества metadata без жёсткого предположения, что Google всегда использует заданный сниппет.

### Canonical, Open Graph и social metadata

- [x] Проверять ровно один canonical на каждой индексируемой странице.
- [x] Проверять, что canonical является абсолютным HTTPS URL без query string.
- [x] Исправить `og:type`: `article` только для статей, `profile` для профилей, `website` для соответствующих страниц.
- [x] Добавить `og:site_name`.
- [x] Добавить `og:locale` и `og:locale:alternate`.
- [ ] Добавить статьям `article:published_time`, `article:modified_time`, `article:author`, `article:section` и `article:tag`.
- [x] Добавить `og:image:alt`, `og:image:type`, `og:image:width` и `og:image:height`.
- [x] Добавить `twitter:title`, `twitter:description`, `twitter:image` и `twitter:image:alt`.
- [ ] Ввести `socialTitle`, `socialDescription`, `socialImage` и `socialImageAlt` с локализованными fallback-правилами.
- [ ] Создать отдельные EN/RU fallback social images.
- [ ] Добавить уникальную social/article image каждой публикации.
- [ ] Добавить корректные social images страницам авторов.
- [x] Добавить `max-image-preview:large` для индексируемых статей.

### Structured data

- [x] Добавить `Organization` с постоянным `@id` на главной.
- [x] Добавить `WebSite` и связать его с `Organization`.
- [x] Вынести проверенные данные организации в единый конфигурационный источник.
- [x] Добавить `BlogPosting` для статей.
- [ ] Передавать в `BlogPosting` canonical `mainEntityOfPage`, headline, description, `inLanguage`, даты, автора, publisher и images.
- [x] Добавить `ProfilePage` + `Person` для человеческих профилей.
- [x] Не размечать Muta как `Person`; определить честную схему publisher/author/contributor для автоматизированного автора.
- [ ] Добавить `BreadcrumbList` для статей, тегов, профилей и авторских архивов.
- [x] Связать Organization, WebSite, BlogPosting и Person через стабильные `@id`.
- [x] Валидировать JSON-LD в автоматических тестах для каждого типа страницы.

### Даты и freshness

- [ ] Добавить явный `lastmod` для содержательно обновляемых статей.
- [ ] Не изменять исходный `date` при редакционных обновлениях.
- [ ] Выводить видимые локализованные подписи `Published` и `Updated`.
- [ ] Синхронизировать `lastmod` в sitemap с `dateModified` в JSON-LD и article metadata.
- [ ] Проверять инвариант `lastmod >= date`.
- [ ] Не использовать время деплоя или неполную Git history как дату содержательного обновления.

### RSS и feed discovery

- [ ] Написать собственный локализованный RSS layout.
- [ ] Исключить профили авторов и служебные sections из общего feed.
- [ ] Сделать основным feed блога либо фильтровать home feed только по публикациям блога.
- [ ] Исправить русские feeds, чтобы title, description и content items были русскими.
- [ ] Исправить авторские feeds, чтобы их состав совпадал с соответствующим HTML-архивом.
- [ ] Удалить пустые технические feeds.
- [ ] Исключить даты `0001` из RSS.
- [ ] Ограничить feed разумным количеством последних публикаций, например 20.
- [ ] Добавить `<link rel="alternate" type="application/rss+xml">` для поддерживаемых feeds.
- [ ] Добавить видимую ссылку на RSS.
- [ ] Использовать стабильные GUID и исходный `pubDate`; обновления отражать отдельно.
- [ ] Добавить XML-тесты состава, языка, порядка, GUID, ссылок и дат feeds.

### Crawlable HTML и внутренняя архитектура

- [ ] Заменить wordmark `div role="link"` на настоящий `<a href>` с локализованной ссылкой на главную.
- [ ] Не перехватывать Cmd/Ctrl-click и другие стандартные действия ссылки JavaScript-кодом.
- [ ] Вынести глобальные header и footer из `<main>`.
- [ ] Оставить один `<main id="main-content">` только вокруг основного содержимого страницы.
- [ ] Добавить skip-link к `#main-content`.
- [ ] Добавить HTML-breadcrumbs для статей, тегов, профилей и авторских архивов.
- [ ] Добавить `aria-current="page"` текущему элементу breadcrumbs и навигации.
- [ ] Добавить обычные crawlable ссылки на Home, Authors и ключевые тематические разделы.
- [ ] Сделать автора в карточках публикаций обычной ссылкой на профиль.
- [ ] Не использовать вложенные ссылки; отделить title/card link от author link семантически.
- [ ] Добавить блок связанных публикаций с обычными `<a href>`.
- [ ] Добавить автоматическую проверку orphan pages отдельно для EN и RU графов ссылок.
- [ ] Проверять все внутренние ссылки, fragment targets и локальные assets.

## P2 — производительность, media и техническое качество

### JavaScript, CSS и сторонние ресурсы

- [ ] Отложить Yandex Metrika до consent, первого взаимодействия или 5–10 секунд.
- [ ] Решить, нужны ли Webvisor и clickmap всем посетителям; при необходимости отключить или семплировать их.
- [ ] Загружать Pantograph только при приближении соответствующей секции.
- [ ] Уменьшить preload `rootMargin` для Tokamak.
- [ ] Добавить Tokamak статичный poster и активировать 3D по viewport/interaction.
- [ ] Отключать тяжёлые 3D-эффекты при `saveData`, reduced motion и на слабых устройствах.
- [ ] Перевести анимацию глаза с вечного `requestAnimationFrame` на event-driven lifecycle.
- [ ] Полностью останавливать RAF при `prefers-reduced-motion` и `document.hidden`.
- [ ] Ставить offscreen-анимации на паузу через IntersectionObserver.
- [ ] Рассмотреть `content-visibility: auto` с корректным `contain-intrinsic-size` для нижних секций.
- [ ] Разделить render-blocking CSS на base/header/footer и page-specific bundles.
- [ ] Измерить необходимость небольшого critical CSS вместо подключения всего stylesheet на каждой странице.
- [ ] Профилировать `backdrop-filter` и другие дорогие эффекты на low-end mobile.

### Image SEO и media pipeline

- [ ] Создать Hugo render hook `render-image.html` для Markdown-изображений.
- [ ] Хранить содержательные иллюстрации как page resources в leaf bundles.
- [ ] Автоматически генерировать `<picture>`, responsive variants, `srcset`, `sizes`, `width` и `height`.
- [ ] Поддержать локализованные alt и figcaption для содержательных изображений.
- [ ] Оставлять `alt=""` только у действительно декоративных изображений.
- [ ] Для содержательных изображений использовать настоящий `src`, а не только `data-deferred-src`, чтобы URL был доступен HTML-парсеру.
- [ ] Предпочесть нативный `loading="lazy"` для обычных offscreen-изображений.
- [ ] Использовать описательные filenames для новых обложек и иллюстраций.
- [ ] Fingerprint-ить изменяемые изображения, особенно social cards и портреты.
- [ ] Проверить внешние ссылки перед удалением неиспользуемых production media assets.
- [ ] Удалить или вынести из production подтверждённо неиспользуемые тяжёлые версии изображений.

### HTTP, compression и caching

- [x] Включить HTTP/2 в реальном TLS server block и проверить ALPN.
- [x] Включить gzip для CSS, JS, SVG, XML и других текстовых ресурсов.
- [x] Добавить `gzip_vary on` и проверять `Vary: Accept-Encoding`.
- [ ] Рассмотреть Brotli после корректной настройки gzip.
- [x] Отдавать fingerprinted CSS/JS с `Cache-Control: public, max-age=31536000, immutable`.
- [ ] Добавить долгий immutable cache техническим iframe-assets только при content-hashed URL.
- [x] Не назначать годовой immutable cache изображениям со стабильными нефингерпринтованными именами.
- [x] Оставить HTML, XML и robots на коротком TTL/revalidation с ETag/Last-Modified.
- [x] Проверять compression и cache headers после каждого production deploy.

### Mobile UX и доступность, влияющие на обход и взаимодействие

- [ ] Не скрывать обе hero CTA на ширине до 620 px.
- [ ] Оставить хотя бы одну primary CTA до hero-изображения на mobile/tablet.
- [ ] Исправить цели hero CTA так, чтобы подпись соответствовала destination.
- [ ] Добавить видимый `:focus-visible` интерактивному coexistence-блоку.
- [ ] Исправить контраст мелкого розового текста в светлой теме.
- [ ] Добавить закрытие мобильного меню по Escape с возвратом фокуса.
- [ ] Локализовать изменяемые JavaScript `aria-label`, включая coexistence и copy controls.
- [ ] Связать значимые `<section>` с заголовками через `aria-labelledby`.
- [ ] Оформить footer links как навигационный landmark.
- [ ] Проверять отсутствие горизонтального overflow на 320/390 px.

## P2 — наблюдаемость и автоматическая защита

### Search Console и webmaster-инструменты

- [ ] Проверить доступы и состояние domain property в Google Search Console.
- [ ] Отправить и проверить `https://mutating.tech/sitemap.xml` в Google Search Console.
- [ ] Проверить Pages/Indexing, Core Web Vitals, manual actions и security issues.
- [ ] Подключить или проверить Bing Webmaster Tools и отправить sitemap.
- [ ] Подключить или проверить Yandex Webmaster и отправить sitemap.
- [ ] Снять baseline за последние 90 дней по query, landing page, language, country и device.
- [ ] Разделить branded и non-branded поисковые данные.

### Real User Monitoring и crawler logs

- [ ] Подключить закреплённую локальную версию `web-vitals`.
- [ ] Собирать LCP, INP, CLS, TTFB и FCP через first-party endpoint.
- [ ] Считать p75 отдельно по mobile/desktop и группам home/article/listing/EN/RU.
- [ ] Передавать release SHA и navigation type вместе с Web Vitals.
- [ ] Не отправлять query strings, PII и свободные DOM-селекторы в telemetry.
- [x] Настроить явный JSON `log_format` nginx для crawl-аналитики.
- [x] Зафиксировать retention и минимизацию IP-данных.
- [x] Ежедневно анализировать Googlebot/Bingbot/YandexBot по 2xx/3xx/404/5xx.
- [x] Анализировать crawler waste по aliases, query URLs и неканоническим адресам.
- [ ] Сверять sitemap URLs, фактически просканированные URL и индексируемые landing pages.
- [ ] Проверять поисковых роботов по официальным IP-диапазонам либо reverse + forward DNS, а не только User-Agent.
- [ ] Добавить alerts на sitemap errors, crawler 404/5xx, ухудшение CWV и исчезновение аналитики после деплоя.

### SEO contract suite и CI

- [x] Создать отдельный `tests/test_seo_contracts.py` для deployment-equivalent production build.
- [x] Собирать в SEO-тестах только настоящий `content/`, без browser fixtures.
- [x] Проверять каждый индексируемый HTML на ровно один непустой title, description, H1 и canonical.
- [x] Проверять self-canonical и HTTPS production-host для всего корпуса.
- [ ] Проверять достижимость каждой индексируемой страницы обычными `<a>` из языковой точки входа либо через явный allowlist.
- [x] Проверять sitemap/indexability contract для всего корпуса.
- [x] Проверять hreflang contract для всего корпуса, а не выборочных URL.
- [ ] Проверять уникальность и язык metadata с минимальным allowlist допустимых дублей.
- [x] Проверять согласованность canonical metadata, Open Graph и Twitter metadata.
- [x] Проверять structured data для каждого типа страницы после её добавления.
- [ ] Проверять RSS discovery, язык, состав, порядок, даты и GUID.
- [ ] Проверять front matter: title, description, уникальный slug, валидные даты, существующий автор и зарегистрированные теги.
- [ ] Добавить production growth-tests для пагинации и tied dates.
- [x] Запускать быстрый SEO-contract job на каждый pull request.
- [ ] Добавить Lighthouse CI для home, blog list, article, tag и author в EN/RU на mobile.
- [ ] Использовать ratchet-бюджеты Lighthouse, а не считать lab-метрики доказательством field CWV.
- [ ] Проверять внешние ссылки по расписанию с retry, не блокируя каждый pull request из-за сторонних сбоев.

## P3 — дополнительные улучшения

- [ ] Добавить `site.webmanifest`, если сайту нужен installable/PWA-сценарий.
- [ ] Добавить иконки 192x192, 512x512 и maskable icon при внедрении manifest.
- [ ] Проверить favicon на соответствие требованиям поисковой выдачи после любых изменений бренда.
- [ ] Добавить видимую RSS-ссылку в footer после исправления feeds.
- [ ] Документировать canonical-host policy, URL migration policy и SEO-инварианты рядом с deployment-документацией.
- [ ] Аннотировать каждый production deploy commit SHA в системах мониторинга.
- [ ] Проводить плановый crawl production-сайта после крупных изменений Hugo, nginx, permalink или multilingual-конфигурации.

## Итоговые критерии готовности

- [ ] Все четыре варианта HTTP/HTTPS и apex/www сводятся за один redirect к `https://mutating.tech/`.
- [ ] Каждый URL в sitemap возвращает `200`, имеет self-canonical, корректный язык, уникальные metadata и достижим обычными ссылками.
- [ ] В индексе нет aliases, технических iframe, ghost sections, английских RU fallbacks и singleton-тегов без самостоятельной ценности.
- [ ] Пагинация, hreflang, RSS и structured data корректны при росте сайта минимум до трёх страниц списков.
- [x] Production build и live deployment автоматически блокируются при нарушении SEO-контрактов.
- [ ] Field CWV измеряется, а performance regression контролируется RUM и CI.
