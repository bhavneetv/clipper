try:
    import pymysql
    pymysql.install_as_MySQLdb()

    # TiDB compatibility patch for Django 5.1+ (bypasses MySQL 8.4+ requirement check)
    from django.db.backends.mysql.base import DatabaseWrapper
    DatabaseWrapper.check_database_version_supported = lambda self: None
except ImportError:
    pass
