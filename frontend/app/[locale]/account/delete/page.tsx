import { DeleteAccountClient } from './client';

// Web account deletion (Google Play requires a web way to delete an account).
// Mirrors the mobile app's flow. Shows a 404 when the backend flag is off.
export default function DeleteAccountPage() {
  return <DeleteAccountClient />;
}
