import { Route, Routes } from "react-router-dom";
import { JobsList } from "./pages/JobsList";
import { JobDetail } from "./pages/JobDetail";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<JobsList />} />
      <Route path="/jobs/:jobId" element={<JobDetail />} />
    </Routes>
  );
}
