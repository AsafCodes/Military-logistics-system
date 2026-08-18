import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from "path"

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,       // Enable access from outside container (0.0.0.0)
    port: 3000,       // Force port 3000
    strictPort: true, // Fail if port is occupied
    watch: {
      usePolling: true, // Critical for Windows+Docker hot reload
    },
  },
})
