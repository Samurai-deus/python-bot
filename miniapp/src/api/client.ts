import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '/',
  timeout: 10_000,
})

let initData = ''

export function setInitData(data: string) {
  initData = data
}

apiClient.interceptors.request.use((config) => {
  if (initData) {
    config.headers['X-Telegram-Init-Data'] = initData
  }
  return config
})

apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg = err.response?.data?.detail ?? err.message ?? 'Unknown error'
    return Promise.reject(new Error(String(msg)))
  }
)
