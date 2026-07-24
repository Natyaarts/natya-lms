import type { Metadata } from "next";
import { Montserrat } from "next/font/google";
import "./globals.css";

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
        {children}
      </body>
    </html>
  );
}
