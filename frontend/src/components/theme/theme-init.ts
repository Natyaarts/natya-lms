// Shared by the root layout (server) and ThemeToggle (client).
export const THEME_STORAGE_KEY = "natya_theme";
export const THEME_COLORS = { dark: "#000000", light: "#f4f4f5" } as const;

// Pages whose design is dark-only (the cinematic landing hero).
export const DARK_ONLY_PATHS = ["/"];

/**
 * Runs inline in <head> before first paint so a saved light theme never
 * flashes dark. Keep it dependency-free and in sync with applyTheme().
 */
export const THEME_INIT_SCRIPT = `(function(){try{if(${JSON.stringify(DARK_ONLY_PATHS)}.indexOf(location.pathname)===-1&&localStorage.getItem("${THEME_STORAGE_KEY}")==="light"){document.documentElement.setAttribute("data-theme","light");var m=document.querySelector('meta[name="theme-color"]');if(m)m.setAttribute("content","${THEME_COLORS.light}");}}catch(e){}})();`;
