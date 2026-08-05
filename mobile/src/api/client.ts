import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';

// Base API URL for production
const API_URL = 'https://academy-api.natyaarts.com/api/';

const client = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add interceptor to inject access token
client.interceptors.request.use(
  async (config) => {
    const token = await AsyncStorage.getItem('access_token');
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

export default client;
