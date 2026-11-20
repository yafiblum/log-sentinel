// vite.config.js: הגדרות Vite, הכלי שמריץ ובונה את ממשק ה-React
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,                 // שלא ימחק הודעות של Tauri מהטרמינל
  server: { port: 1420, strictPort: true },  // Tauri מצפה לפורט קבוע
});
