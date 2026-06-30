import { useCallback, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { BarChart3, BookOpenCheck, Database, FlaskConical, History, RefreshCw } from "lucide-react";
import { checkHealth } from "./api";
import { readCurrentDifyUrl, RUN_DIFY_URL_CHANGED_EVENT, RUN_DIFY_URL_STORAGE_KEY } from "./difySource";
import type { HealthStatus } from "./types";
import { DatasetEditor } from "./pages/DatasetEditor";
import { Datasets } from "./pages/Datasets";
import { Home } from "./pages/Home";
import { RunDetail } from "./pages/RunDetail";
import { RunCompare } from "./pages/RunCompare";
import { Runs } from "./pages/Runs";
import { ErrorToastContainer } from "./widgets/ErrorToast";

type HealthTone = "checking" | "ok" | "warn" | "down";

function describeHealth(health: HealthStatus, tone: HealthTone): { label: string; tip: string } {
  if (tone === "checking") {
    return { label: "正在检查后端…", tip: "向 /api/health 发送探测请求" };
  }
  if (health.ok) {
    return {
      label: `评测后端正常 · v${health.version}`,
      tip: `后端最近一次健康检查通过（${new Date().toLocaleTimeString()}）`
    };
  }
  if (health.status === "unreachable") {
    return {
      label: "无法连接后端（/api/health 无响应）",
      tip: health.error || "确认后端服务是否启动，或检查 Vite 代理配置"
    };
  }
  return {
    label: `后端返回异常状态：${health.status}`,
    tip: health.error || "查看后端日志确认原因"
  };
}

function toneOf(health: HealthStatus, isChecking: boolean): HealthTone {
  if (isChecking && !health.service) return "checking";
  if (health.ok) return "ok";
  if (health.status === "unreachable") return "down";
  return "warn";
}

const HEALTH_REFRESH_MS = 15_000;

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const isRunChild = location.pathname.startsWith("/runs/");
  const [currentDifyUrl, setCurrentDifyUrl] = useState<string | null>(() => readCurrentDifyUrl());
  const [health, setHealth] = useState<HealthStatus>({
    ok: false,
    status: "unknown",
    service: "",
    version: ""
  });
  const [checking, setChecking] = useState(true);

  const probeHealth = useCallback(async () => {
    setChecking(true);
    const next = await checkHealth();
    setHealth(next);
    setChecking(false);
  }, []);

  useEffect(() => {
    void probeHealth();
    const timer = window.setInterval(() => {
      void probeHealth();
    }, HEALTH_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [probeHealth]);

  useEffect(() => {
    const syncCurrentDifyUrl = () => setCurrentDifyUrl(readCurrentDifyUrl());
    const syncFromStorage = (event: StorageEvent) => {
      if (event.key === RUN_DIFY_URL_STORAGE_KEY) syncCurrentDifyUrl();
    };
    window.addEventListener(RUN_DIFY_URL_CHANGED_EVENT, syncCurrentDifyUrl);
    window.addEventListener("storage", syncFromStorage);
    return () => {
      window.removeEventListener(RUN_DIFY_URL_CHANGED_EVENT, syncCurrentDifyUrl);
      window.removeEventListener("storage", syncFromStorage);
    };
  }, []);

  const tone = toneOf(health, checking);
  const { label, tip } = describeHealth(health, tone);
  const appVersion = import.meta.env.VITE_APP_VERSION ? `v${import.meta.env.VITE_APP_VERSION}` : "";
  const showHeaderStatus = tone !== "ok";

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <header className="topbar" role="banner">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true">
            <BookOpenCheck size={22} />
          </div>
          <div>
            <div className="brand-title-row">
              <h1>Dify-KB-Eval</h1>
              {appVersion && <span className="brand-version">{appVersion}</span>}
            </div>
            <p>Dify 知识库召回评测工具</p>
          </div>
        </div>
        <nav className="topnav" aria-label="主导航">
          <NavLink to="/" end className={({ isActive }) => `nav-pill ${isActive && !isRunChild ? "active" : ""}`}>
            <FlaskConical size={16} aria-hidden="true" />
            评测台
          </NavLink>
          <NavLink to="/runs" className={({ isActive }) => `nav-pill ${isActive && !isRunChild ? "active" : ""}`}>
            <History size={16} aria-hidden="true" />
            历史评测
          </NavLink>
          <NavLink to="/compare" className={({ isActive }) => `nav-pill ${isActive ? "active" : ""}`}>
            <BarChart3 size={16} aria-hidden="true" />
            分析对比
          </NavLink>
          <NavLink to="/datasets" className={({ isActive }) => `nav-pill ${isActive ? "active" : ""}`}>
            <Database size={16} aria-hidden="true" />
            评测集
          </NavLink>
        </nav>
        <div className="header-meta">
          {currentDifyUrl && (
            <button
              type="button"
              className="header-source-chip"
              onClick={() => navigate("/?from=topbar-source")}
              title={`当前数据源：${currentDifyUrl}。点击返回评测台切换`}
            >
              <Database size={14} aria-hidden="true" />
              <span className="header-source-chip-label">数据源</span>
              <code className="header-source-chip-url">{currentDifyUrl}</code>
            </button>
          )}
          {showHeaderStatus && (
            <div
              className={`header-status header-status--${tone}`}
              role="status"
              aria-live="polite"
              aria-atomic="true"
              title={tip}
            >
              <span className="status-dot" aria-hidden="true" />
              <span className="header-status-label">{label}</span>
              <button
                type="button"
                className="icon-button tiny header-status-refresh"
                onClick={() => void probeHealth()}
                disabled={checking}
                aria-label="重新检查后端健康状态"
                title="重新检查"
              >
                <RefreshCw size={14} className={checking ? "spin" : ""} aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      </header>

      <main id="main-content" className="main-stage" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/compare" element={<RunCompare />} />
          <Route path="/runs/:runId" element={<RunDetail />} />
          <Route path="/datasets" element={<Datasets />} />
          <Route path="/datasets/:path/editor" element={<DatasetEditor />} />
        </Routes>
      </main>
      <ErrorToastContainer />
    </div>
  );
}
