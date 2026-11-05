import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig(({mode})=>{
  const env=loadEnv(mode,'.','MDS_')
  const apiTarget=process.env.MDS_API_URL||env.MDS_API_URL||'http://localhost:8000'
  // ketcher-react calls require('raphael') from a module that also uses ESM
  // syntax. The dev server's esbuild prebundle rewrites it, but a production
  // build leaves the bare require and the editor dies on open, so ask the
  // CommonJS plugin to transform these mixed modules too.
  return {plugins:[react()],define:{global:'globalThis','process.env':{}},build:{commonjsOptions:{transformMixedEsModules:true}},server:{port:5173,proxy:{'/api':apiTarget}}}
})
