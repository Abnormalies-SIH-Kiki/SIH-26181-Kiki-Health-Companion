import { redirect } from 'next/navigation';
import db from '@/lib/db';

export default function Home() {
  try {
    const users = db.prepare('SELECT * FROM users LIMIT 1').all();
    if (users.length === 0) {
      redirect('/onboarding/personal-details');
    } else {
      redirect('/dashboard');
    }
  } catch (error) {
    console.error('Error fetching users:', error);
    redirect('/onboarding/personal-details');
  }
}
