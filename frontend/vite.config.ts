import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig(({mode})=>{
  const env=loadEnv(mode,'.','MDS_')
  const apiTarget=process.env.MDS_API_URL||env.MDS_API_URL||'http://localhost:8000'
  return {plugins:[react()],define:{global:'globalThis','process.env':{}},server:{port:5173,proxy:{'/api':apiTarget}}}
})
