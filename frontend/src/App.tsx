import { DashboardPage } from "./pages/DashboardPage";
import { useDashboard } from "./hooks/useDashboard";
import { translate, type Language } from "./i18n";
import { useState } from "react";

export default function App() {
  const { data, error, refreshing, refresh } = useDashboard();
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem("qhome.lang") === "tr" ? "tr" : "en");
  const t = (key: Parameters<typeof translate>[1]) => translate(language, key);

  if (!data && refreshing) return <main className="app-state"><span className="spinner" /><p>{t("connecting")}</p></main>;
  if (!data) return <main className="app-state"><div className="error-panel"><span className="error-mark">!</span><h1>{t("backendUnavailable")}</h1><p>{error || t("noResponse")}</p><p className="muted">{t("startBackend")}</p><button className="primary-button" onClick={() => void refresh(true)}>{t("retry")}</button></div></main>;

  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="#dashboard"><span className="brand-symbol">Q</span><span>QHome</span></a><nav aria-label="Main navigation"><a className="nav-item active" href="#dashboard"><span>⌂</span>{t("dashboard")}</a><span className="nav-item nav-disabled" aria-disabled="true"><span>▦</span>{t("boards")}</span><span className="nav-item nav-disabled" aria-disabled="true"><span>▧</span>{t("automations")}</span></nav><div className="sidebar-bottom"><div className={`backend-status ${error ? "backend-offline" : ""}`}><span className={error ? "offline-pulse" : "live-pulse"} />{error ? t("offline") : t("connected")}</div><div className="language-toggle" aria-label={t("language")}><button className={language === "en" ? "selected" : ""} onClick={() => { localStorage.setItem("qhome.lang", "en"); setLanguage("en"); }}>EN</button><button className={language === "tr" ? "selected" : ""} onClick={() => { localStorage.setItem("qhome.lang", "tr"); setLanguage("tr"); }}>TR</button></div><span className="app-version">QHome {data.meta.version}</span></div></aside>
    <main className="main-content"><DashboardPage data={data} language={language} /></main>
    {error && <div className="connection-warning" role="status">{t("interrupted")}</div>}
  </div>;
}
