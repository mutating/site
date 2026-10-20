import { createRoot } from "react-dom/client";
import { createMutatingHomeHeader } from "../src";
import { Header, ThemeProvider } from "../src/react";
import "../src/styles/index.css";
import "./demo.css";

const language = new URLSearchParams(location.search).get("language") === "ru" ? "ru" : "en";
document.documentElement.lang = language;
createRoot(document.getElementById("root")!).render(
  <ThemeProvider>
    <Header {...createMutatingHomeHeader(language)} />
    <main className="tmc-shell demo-content">
      <p className="tmc-eyebrow">@mutating/ui · mutating.tech</p>
      <h1>{language === "ru" ? "Конфигурация главной" : "The home configuration"}<span>.</span></h1>
      <p>{language === "ru" ? "Обычный компонент Header с меню, логотипом, языками и ссылкой главной страницы." : "The shared Header with the home page's menu, brand, languages, and adjacent link."}</p>
      <div className="demo-row"><a className="tmc-text-link" href="/home.html?language=en">English</a><a className="tmc-text-link" href="/home.html?language=ru">Русский</a><a className="tmc-text-link" href="/react.html">Custom menu →</a></div>
    </main>
  </ThemeProvider>,
);
