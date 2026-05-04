import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#15161a",
        paper: "#f7f5ef",
        panel: "#fffefa",
        line: "#ded8ca",
        moss: "#58745d",
        marine: "#1d5f73",
        clay: "#b45b45",
        gold: "#c4933c",
      },
      boxShadow: {
        soft: "0 18px 60px rgba(21, 22, 26, 0.10)",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
} satisfies Config;
