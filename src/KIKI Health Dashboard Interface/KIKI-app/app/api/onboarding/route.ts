import { NextResponse } from 'next/server';
import db from '@/lib/db';
import { revalidatePath } from 'next/cache';

export async function POST(request: Request) {
  try {
    const data = await request.json();
    const { name, gender, age, city, country, steps, calories } = data;

    const [firstName, ...lastNameParts] = (name || 'User').trim().split(' ');
    const lastName = lastNameParts.join(' ');

    const stmt = db.prepare(`
      INSERT INTO users (first_name, last_name, age, gender, location, daily_steps_goal, daily_calories_goal)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `);

    const result = stmt.run(
      firstName || 'User',
      lastName || '',
      parseInt(age) || 25,
      gender || 'Prefer not to say',
      `${city || 'San Francisco'}, ${country || 'US'}`,
      parseInt(steps) || 8000,
      parseInt(calories) || 500
    );

    revalidatePath('/');
    revalidatePath('/dashboard');
    revalidatePath('/profile');
    revalidatePath('/weather');
    revalidatePath('/sleep');
    revalidatePath('/onboarding/personal-details');

    return NextResponse.json({ success: true, userId: result.lastInsertRowid });
  } catch (error) {
    console.error('Error in onboarding API:', error);
    return NextResponse.json({ success: false, error: 'Failed to save user' }, { status: 500 });
  }
}
