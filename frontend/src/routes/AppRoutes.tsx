import { Navigate, Route, Routes } from 'react-router-dom';
import ProtectedRoute from '../components/auth/ProtectedRoute';
import AppLayout from '../layouts/AppLayout';
import Login from '../features/auth/pages/Login';
import Dashboard from '../features/dashboard/pages/Dashboard';
import EmployeesPage from '../features/employees/pages/EmployeesPage';
import CreateEmployee from '../features/employees/pages/CreateEmployee';
import InstantLeads from '../features/instant-leads/pages/InstantLeads';
import CampaignsPage from '../features/campaigns/pages/CampaignsPage';
import CampaignDetailPage from '../features/campaigns/pages/CampaignDetailPage';
import CreateCampaignPage from '../features/campaigns/pages/CreateCampaignPage';
import PhoneNumbersPage from '../features/phone-numbers/pages/PhoneNumbersPage';
import BillingPage from '../features/billing/pages/BillingPage';
import SettingsPage from '../features/settings/pages/SettingsPage';
import CallsPage from '../features/calls/pages/CallsPage';
import IntegrationsPage from '../features/integrations/pages/IntegrationsPage';
import NotFound from '../features/not-found/pages/NotFound';
import ChangePassword from '../features/auth/pages/ChangePassword';
import { useAuth } from '../features/auth/hooks/useAuth';
import AdminPage from '../features/admin/pages/AdminPage';

function AdminOnly() {
  const { user } = useAuth();
  return user?.role === 'admin' ? <AdminPage /> : <Navigate to="/dashboard" replace />;
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/change-password" element={<ChangePassword />} />
      <Route path="/admin" element={<ProtectedRoute><AdminOnly /></ProtectedRoute>} />
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/employees" element={<EmployeesPage />} />
        <Route path="/employees/new" element={<CreateEmployee />} />
        <Route path="/employees/:id" element={<CreateEmployee />} />
        <Route path="/instant" element={<InstantLeads />} />
        <Route path="/campaigns" element={<CampaignsPage />} />
        <Route path="/campaigns/new" element={<CreateCampaignPage />} />
        <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/numbers" element={<PhoneNumbersPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="/billing" element={<BillingPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
