"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

type Theme = "light" | "dark" | "system";

interface ThemeContextValue {
  theme: Theme;
  resolved: "light" | "dark";
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);
const STORAGE_KEY = "tradeloom-theme";

function systemTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("system");
  const [resolved, setResolved] = useState<"light" | "dark">("light");

  const apply = useCallback((next: Theme) => {
    const effective = next === "system" ? systemTheme() : next;
    document.documentElement.classList.toggle("dark", effective === "dark");
    document.documentElement.style.colorScheme = effective;
    setResolved(effective);
  }, []);

  useEffect(() => {
    const stored = (window.localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "system";
    setThemeState(stored);
    apply(stored);

    // Follow the OS while the preference is "system".
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if ((window.localStorage.getItem(STORAGE_KEY) as Theme | null) !== "dark" && (window.localStorage.getItem(STORAGE_KEY) as Theme | null) !== "light") {
        apply("system");
      }
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [apply]);

  const setTheme = useCallback(
    (next: Theme) => {
      window.localStorage.setItem(STORAGE_KEY, next);
      setThemeState(next);
      apply(next);
    },
    [apply],
  );

  return (
    <ThemeContext.Provider value={{ theme, resolved, setTheme }}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside <ThemeProvider>");
  return context;
}
