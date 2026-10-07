import React, { useEffect, useState } from 'react';

const ReportPage: React.FC = () => {
  const [session, setSession] = useState<{name: string; csrf: string; needs_consent: boolean} | null>(null);
  const [initialized, setInitialized] = useState(false);
  const day = (offset: number) => new Date(Date.now() - offset * 86400000).toISOString().slice(0, 10);
  const [start, setStart] = useState(day(7));
  const [end, setEnd] = useState(day(0));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/session', { credentials: 'include' }).then(async response => {
      if (response.ok) setSession(await response.json());
    }).catch(() => setError('Сервис недоступен')).finally(() => setInitialized(true));
  }, []);

  const downloadReport = async () => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch(`/api/reports?${new URLSearchParams({start, end})}`, {credentials: 'include'});
      const data = await response.json();
      if (response.status === 401) setSession(null);
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
      const download = await fetch(data.url, {credentials: 'include'});
      if (!download.ok) throw new Error('Не удалось скачать отчёт');
      const url = URL.createObjectURL(await download.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `report-${start}-${end}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось получить отчёт');
    } finally {
      setLoading(false);
    }
  };

  if (!initialized) {
    return <div>Загрузка…</div>;
  }

  if (!session) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
        <button
          onClick={() => window.location.assign('/auth/login')}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
        >
          Войти
        </button>
        <a href="/auth/login?provider=yandex" className="mt-4">Войти через Яндекс ID</a>
        {error && <p role="alert">{error}</p>}
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      <div className="p-8 bg-white rounded-lg shadow-md">
        <h1 className="text-2xl font-bold mb-6">Отчёты о работе протеза</h1>
        <p className="mb-4">{session.name}</p>
        {session.needs_consent && <div className="mb-4">
          <p>Разрешить сохранить идентификатор, имя и почту из Яндекс ID в BionicPRO для работы с профилем?</p>
          <button disabled={loading} onClick={async () => {
            setLoading(true);
            try {
              const response = await fetch('/api/consent', {method: 'POST', credentials: 'include', headers: {'X-CSRF-Token': session.csrf}});
              if (!response.ok) throw new Error('Не удалось сохранить согласие');
              setSession({...session, needs_consent: false});
            } catch (err) { setError(String(err)); } finally { setLoading(false); }
          }}>Разрешить</button>
        </div>}
        <label className="block mb-2">С <input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
        <label className="block mb-4">До (не включительно) <input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
        
        <button
          onClick={downloadReport}
          disabled={loading || session.needs_consent}
          className={`px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 ${
            loading ? 'opacity-50 cursor-not-allowed' : ''
          }`}
        >
          {loading ? 'Подготовка…' : 'Скачать отчёт'}
        </button>
        <button className="ml-4" disabled={loading} onClick={async () => {
          setLoading(true);
          try {
            const result = await fetch('/auth/logout', {method: 'POST', credentials: 'include', headers: {'X-CSRF-Token': session.csrf}});
            if (!result.ok) throw new Error('Не удалось выйти');
            setSession(null);
          } catch (err) { setError(String(err)); } finally { setLoading(false); }
        }}>Выйти</button>

        {error && (
          <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}
      </div>
    </div>
  );
};

export default ReportPage;
