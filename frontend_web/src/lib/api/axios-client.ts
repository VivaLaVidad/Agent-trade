import axios from "axios";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8900";

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

apiClient.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("hw_token");
    if (token) {
      config.headers["X-Hardware-Token"] = token;
    }
  }
  return config;
});

apiClient.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status;
    if (status === 401) {
      console.error("[API] Unauthorized — invalid hardware token");
    } else if (status === 500) {
      console.error("[API] Server error:", error.response?.data?.detail);
    }
    return Promise.reject(error);
  },
);
