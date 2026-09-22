import type { Metadata } from "next";
import { Montserrat } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/context/AuthContext";
import AppHeader from "@/components/navigation/AppHeader";

const montserrat = Montserrat({
  variable: "--font-montserrat",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Natya LMS | Learn Classical Arts Online",
  description: "Learn Indian classical arts from the best masters.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${montserrat.variable} font-sans antialiased bg-black text-white selection:bg-[#facc15] selection:text-black min-h-full flex flex-col relative`}>
        <AuthProvider>
          <AppHeader />
          <div className="flex-1 flex flex-col">
            {children}
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
