import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource/iosevka/800.css";
import "@fontsource/iosevka/900.css";
import "@fontsource/fira-sans/400.css";
import "@fontsource/fira-sans/600.css";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<React.StrictMode><BrowserRouter><App /></BrowserRouter></React.StrictMode>);
