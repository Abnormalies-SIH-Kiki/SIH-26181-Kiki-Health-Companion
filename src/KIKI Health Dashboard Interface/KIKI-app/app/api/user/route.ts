import { NextResponse } from 'next/server';
import db from '@/lib/db';
import { revalidatePath } from 'next/cache';

export async function GET() {
  try {
    const users = db.prepare('SELECT * FROM users LIMIT 1').all() as any[];
    if (users.length === 0) {
      return NextResponse.json({ success: false, error: 'No user found' }, { status: 404 });
    }
    return NextResponse.json({ success: true, user: users[0] });
  } catch (error) {
    console.error('Error fetching user:', error);
    return NextResponse.json({ success: false, error: 'Database error' }, { status: 500 });
  }
}

export async function PUT(request: Request) {
  try {
    const body = await request.json();
    const users = db.prepare('SELECT * FROM users LIMIT 1').all() as any[];
    if (users.length === 0) {
      return NextResponse.json({ success: false, error: 'No user found' }, { status: 404 });
    }
    const userId = users[0].id;

    // Check if updating profile details
    if (body.type === 'profile' || body.name !== undefined) {
      const fullName = (body.name || '').trim();
      const parts = fullName.split(' ');
      const firstName = parts[0] || users[0].first_name;
      const lastName = parts.slice(1).join(' ') || '';
      const gender = body.gender !== undefined ? body.gender : users[0].gender;
      const age = body.age !== undefined ? parseInt(body.age) : users[0].age;

      db.prepare(`
        UPDATE users 
        SET first_name = ?, last_name = ?, gender = ?, age = ?
        WHERE id = ?
      `).run(firstName, lastName, gender, age, userId);
    }

    // Check if updating daily goals
    if (body.type === 'goals' || body.steps !== undefined || body.calories !== undefined) {
      const steps = body.steps !== undefined ? parseInt(body.steps) : users[0].daily_steps_goal;
      const calories = body.calories !== undefined ? parseInt(body.calories) : users[0].daily_calories_goal;

      db.prepare(`
        UPDATE users 
        SET daily_steps_goal = ?, daily_calories_goal = ?
        WHERE id = ?
      `).run(steps, calories, userId);
    }

    revalidatePath('/profile');
    revalidatePath('/dashboard');
    revalidatePath('/weather');
    revalidatePath('/sleep');

    const updatedUser = db.prepare('SELECT * FROM users WHERE id = ?').get(userId);
    return NextResponse.json({ success: true, user: updatedUser });
  } catch (error) {
    console.error('Error updating user:', error);
    return NextResponse.json({ success: false, error: 'Failed to update user' }, { status: 500 });
  }
}

export async function DELETE() {
  try {
    db.prepare('DELETE FROM health_data').run();
    db.prepare('DELETE FROM users').run();

    revalidatePath('/');
    revalidatePath('/profile');
    revalidatePath('/dashboard');
    revalidatePath('/weather');
    revalidatePath('/sleep');
    revalidatePath('/onboarding/personal-details');

    return NextResponse.json({ success: true, message: 'Account deleted successfully' });
  } catch (error) {
    console.error('Error deleting account:', error);
    return NextResponse.json({ success: false, error: 'Failed to delete account' }, { status: 500 });
  }
}
