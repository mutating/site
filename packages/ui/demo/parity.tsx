// Test/demo harness only. The real site and platform do not import this file.
import { useEffect } from "react";
import { hydrateRoot } from "react-dom/client";
import { mountHeader, type HeaderConfig } from "../src";
import { Header, ThemeProvider, useTheme } from "../src/react";

const config: HeaderConfig = JSON.parse(document.querySelector("#header-config")!.textContent!);
function Ready() {
  const { controller } = useTheme();
  useEffect(() => { if (controller) document.documentElement.dataset.uiReady = "true"; }, [controller]);
  return null;
}
document.fonts.ready.then(() => {
  if (document.documentElement.dataset.uiAdapter === "react") {
    hydrateRoot(document.querySelector("#header-root")!, <ThemeProvider><Header {...config} /><Ready /></ThemeProvider>);
  } else {
    mountHeader(document.querySelector(".tmc-header-shell")!);
    document.documentElement.dataset.uiReady = "true";
  }
});
