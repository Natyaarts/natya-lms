import type { Metadata, Viewport } from "next";
import { Montserrat } from "next/font/google";
import "./globals.css";
import "./theme-light.css";
import { AuthProvider } from "@/context/AuthContext";
import AppHeader from "@/components/navigation/AppHeader";
import PwaSetup from "@/components/pwa/PwaSetup";
import { THEME_INIT_SCRIPT } from "@/components/theme/theme-init";

const montserrat = Montserrat({
  variable: "--font-montserrat",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Natya LMS | Learn Classical Arts Online",
  description: "Learn Indian classical arts from the best masters.",
  applicationName: "Natya",
  appleWebApp: {
    capable: true,
    title: "Natya",
    statusBarStyle: "black",
  },
  icons: {
    apple: "/apple-touch-icon.png",
  },
};

export const viewport: Viewport = {
  themeColor: "#000000",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // suppressHydrationWarning: the init script may set data-theme before React hydrates.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className={`${montserrat.variable} font-sans antialiased bg-black text-white selection:bg-[#facc15] selection:text-black min-h-full flex flex-col relative`}>
        <AuthProvider>
          <AppHeader />
          <div className="flex-1 flex flex-col">
            {children}
          </div>
          <PwaSetup />
        </AuthProvider>
      </body>
    </html>
  );
}
