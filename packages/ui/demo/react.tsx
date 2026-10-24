import { StrictMode, useState, type ComponentPropsWithoutRef } from "react";
import { createRoot } from "react-dom/client";
import { Button, Dropdown, Header, SquareButton, ThemeProvider, ThemeToggle, useTheme } from "../src/react";
import "../src/styles/index.css";
import "./demo.css";

const language = { current: "ru", label: "Выбрать язык", groupLabel: "Языки", options: [{ code: "ru", href: "#ru" }, { code: "en", href: "#en" }] };
const items = [{ id: "components", label: "Компоненты", href: "#components" }, { id: "html", label: "HTML-версия", href: "/" }];
const brandQuery = new URLSearchParams(location.search);
function DemoRouterLink(props: ComponentPropsWithoutRef<"a">) {
  return <a {...props} data-demo-router-link="" />;
}
function Showcase() {
  const [mounted, setMounted] = useState(true);
  const [count, setCount] = useState(0);
  const [otherMenu, setOtherMenu] = useState(false);
  const [extraLanguage, setExtraLanguage] = useState(false);
  const [currentLanguage, setCurrentLanguage] = useState("ru");
  const [wrappedLinks, setWrappedLinks] = useState(false);
  const [squareCount, setSquareCount] = useState(0);
  const [squareContent, setSquareContent] = useState(0);
  const [dropdownMounted, setDropdownMounted] = useState(true);
  const [dropdownAlternate, setDropdownAlternate] = useState(false);
  const [dropdownCount, setDropdownCount] = useState(0);
  const [dropdownTrigger, setDropdownTrigger] = useState(false);
  const { theme, controller } = useTheme();
  return <>
    {mounted && <Header
      brand={{ href: brandQuery.get("brand-home") ?? "/react.html", animation: brandQuery.has("reload-brand") ? { activation: "reload-home" } : undefined }} items={brandQuery.has("language-only") ? [] : otherMenu ? [{ id: "overview", href: "#overview", label: "Обзор", active: true }, { id: "organizations", href: "#organizations", label: "Организации" }] : items} navigationLabel="Навигация"
      language={{ ...language, current: currentLanguage, options: extraLanguage ? [...language.options, { code: "de", href: "#de" }] : language.options }} themeLabel="Переключить тему"
      menuLabel="Открыть меню" menuText="Меню"
      adjacentLink={brandQuery.has("square-button-header") ? {
        href: "#components", label: "Свободное содержимое",
        children: squareContent === 0 ? <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2 22 12 12 22 2 12Z" /></svg>
          : squareContent === 1 ? <span aria-hidden="true">UI</span>
          : <img src="/src/assets/muta-eye-surface-alpha-v1.webp" width="32" height="24" alt="" />,
      } : undefined}
      renderLink={wrappedLinks ? (_item, attributes, children) => <DemoRouterLink {...attributes}>{children}</DemoRouterLink> : undefined}
    />}
    <main className="tmc-shell demo-content" id="components">
      <p className="tmc-eyebrow">@mutating/ui · React</p>
      <h1>Общий визуальный язык<span>.</span></h1>
      <p>Компоненты используют общие стили и поведение. Текущая тема: <strong data-testid="theme">{theme}</strong>.</p>
      <div className="demo-grid">
        <section className="tmc-surface demo-panel"><h2>Кнопки</h2><div className="demo-row">
          <Button onClick={() => setCount((value) => value + 1)}>Действие →</Button>
          <Button variant="secondary">Вторичная</Button>
          <Button variant="dark" size="compact">Компактная</Button>
          <Button disabled>Недоступна</Button>
        </div><p>Выполнено действий: <span data-testid="count">{count}</span></p></section>
        <section className="tmc-surface demo-panel"><h2>Квадратная кнопка</h2>
          <div className="demo-row">
            <SquareButton href="#components" aria-label="Квадратная ссылка"><span aria-hidden="true">UI</span></SquareButton>
            <SquareButton onClick={() => setSquareCount(value => value + 1)} aria-label="Квадратное действие"><svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2 22 12 12 22 2 12Z" /></svg></SquareButton>
            <SquareButton disabled onClick={() => setSquareCount(value => value + 1)} aria-label="Квадратная недоступная"><span aria-hidden="true">×</span></SquareButton>
            <Button variant="secondary" size="compact" onClick={() => setSquareContent(value => (value + 1) % 3)}>Сменить содержимое</Button>
          </div>
          <p>Выполнено действий: <span data-testid="square-count">{squareCount}</span></p>
        </section>
        <section className="tmc-surface demo-panel"><h2>Выпадающее меню</h2>
          <div className="demo-row">
            {dropdownMounted && <Dropdown label="Меню примера" trigger={attributes => <SquareButton key={String(dropdownTrigger)} {...attributes}>UI</SquareButton>}>
              <a role="menuitem" href={dropdownAlternate ? "#overview" : "#components"}>{dropdownAlternate ? "Обзор" : "Компоненты"}</a>
              <button role="menuitem" type="button" disabled>Пропустить</button>
              <button role="menuitem" type="button" onClick={() => setDropdownCount(value => value + 1)}>Выполнить</button>
            </Dropdown>}
            <Button id="change-dropdown-items" onClick={() => setDropdownAlternate(value => !value)}>Изменить пункты</Button>
            <Button onClick={() => setDropdownTrigger(value => !value)}>Заменить кнопку меню</Button>
            <Button onClick={() => setDropdownMounted(value => !value)}>Пересоздать меню</Button>
          </div>
          <p>Действий в меню: <span data-testid="dropdown-count">{dropdownCount}</span></p>
        </section>
        <section className="tmc-surface tmc-surface--raised demo-panel"><h2>Тема и жизненный цикл</h2>
          <div className="demo-row"><ThemeToggle aria-label="Второй переключатель темы" />
            <Button variant="secondary" size="compact" onClick={() => controller?.useSystemTheme()}>Системная тема</Button>
            <Button variant="secondary" size="compact" onClick={() => setMounted((value) => !value)}>Пересоздать хэдер</Button>
            <Button variant="secondary" size="compact" onClick={() => setOtherMenu((value) => !value)}>Другое меню</Button>
            <Button variant="secondary" size="compact" onClick={() => setExtraLanguage((value) => !value)}>Другие языки</Button>
            <Button variant="secondary" size="compact" onClick={() => setCurrentLanguage((value) => value === "ru" ? "en" : "ru")}>Сменить текущий язык</Button>
            <Button variant="secondary" size="compact" onClick={() => setWrappedLinks((value) => !value)}>Другие ссылки</Button>
          </div>
        </section>
      </div>
      <p className="demo-footer"><a className="tmc-text-link" href="/">Открыть HTML-версию →</a></p>
    </main>
  </>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><ThemeProvider><Showcase /></ThemeProvider></StrictMode>);
