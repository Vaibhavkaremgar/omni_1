import supabase from './db-client.js';

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') return res.status(204).end();

  try {
    if (req.method === 'GET') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      const client = token
        ? new (await import('@supabase/supabase-js')).default(
            process.env.NEXT_PUBLIC_SUPABASE_URL,
            process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
            { global: { headers: { Authorization: `Bearer ${token}` } } }
          )
        : supabase;

      const { query } = req;
      let q = client.from('phone_numbers').select('*, employees (name)').order('created_at', { ascending: false });

      if (query.status) q = q.eq('status', query.status);

      const { data, error } = await q;
      if (error) throw error;
      return res.status(200).json(data);
    }

    if (req.method === 'POST') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      if (!token) return res.status(401).json({ error: 'Unauthorized' });

      const { createClient } = await import('@supabase/supabase-js');
      const client = new (await import('@supabase/supabase-js')).default(
        process.env.NEXT_PUBLIC_SUPABASE_URL,
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        { global: { headers: { Authorization: `Bearer ${token}` } } }
      );

      const { data: { user }, error: userError } = await client.auth.getUser(token);
      if (userError || !user) return res.status(401).json({ error: 'Invalid token' });

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      const { number, country, provider } = body;

      const { data, error } = await client.from('phone_numbers').insert({
        user_id: user.id,
        number, country, provider,
        status: 'available',
      }).select().single();

      if (error) throw error;
      return res.status(201).json(data);
    }

    if (req.method === 'PUT') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      if (!token) return res.status(401).json({ error: 'Unauthorized' });

      const { createClient } = await import('@supabase/supabase-js');
      const client = createClient(
        process.env.NEXT_PUBLIC_SUPABASE_URL,
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        { global: { headers: { Authorization: `Bearer ${token}` } } }
      );

      const { data: { user }, error: userError } = await client.auth.getUser(token);
      if (userError || !user) return res.status(401).json({ error: 'Invalid token' });

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      const { id, assigned_employee_id } = body;

      const { data, error } = await client
        .from('phone_numbers')
        .update({
          assigned_employee_id: assigned_employee_id || null,
          status: assigned_employee_id ? 'assigned' : 'available',
          updated_at: new Date().toISOString(),
        })
        .eq('id', id)
        .eq('user_id', user.id)
        .select()
        .single();

      if (error) throw error;
      return res.status(200).json(data);
    }

    if (req.method === 'DELETE') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      if (!token) return res.status(401).json({ error: 'Unauthorized' });

      const { createClient } = await import('@supabase/supabase-js');
      const client = createClient(
        process.env.NEXT_PUBLIC_SUPABASE_URL,
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        { global: { headers: { Authorization: `Bearer ${token}` } } }
      );

      const { data: { user }, error: userError } = await client.auth.getUser(token);
      if (userError || !user) return res.status(401).json({ error: 'Invalid token' });

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      const { id } = body;

      const { error } = await client
        .from('phone_numbers')
        .delete()
        .eq('id', id)
        .eq('user_id', user.id);

      if (error) throw error;
      return res.status(200).json({ ok: true });
    }

    res.status(405).json({ error: 'Method not allowed' });
  } catch (err) {
    console.error('Phone numbers API error:', err);
    res.status(500).json({ error: err.message });
  }
}
