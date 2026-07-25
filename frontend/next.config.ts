import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'images.unsplash.com',
      },
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '8000',
      },
      {
        protocol: 'http',
        hostname: 'natya-lms-backend-env.eba-5tjxp8qg.ap-south-1.elasticbeanstalk.com',
      },
    ],
  },
};

export default nextConfig;
