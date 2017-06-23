import cProfile

#------------------------------------------------------------------------------
# Debug
#------------------------------------------------------------------------------
#
#    This file contains random snippets of code used while developing and
#    debugging parts of lighthouse. I don't expect any of this code to be
#    active or in use for major releases.
#

#------------------------------------------------------------------------------
# Call Profiling
#------------------------------------------------------------------------------

pr = cProfile.Profile()

def profile(func):
    """
    A simple function profiling decorator.
    """
    def wrap(*args, **kwargs):
        global pr
        pr.enable()
        result = func(*args, **kwargs)
        pr.disable()
        pr.print_stats(sort="tottime")
        return result
    return wrap

#------------------------------------------------------------------------------
# Line Profiler
#------------------------------------------------------------------------------

#
# Function Profiling
#

# from: https://gist.github.com/sibelius/3920b3eb5adab482b105
try:
    from line_profiler import LineProfiler
    def line_profile(func):
        def profiled_func(*args, **kwargs):
            try:
                profiler = LineProfiler()
                profiler.add_function(func)
                profiler.enable_by_count()
                return func(*args, **kwargs)
            finally:
                profiler.print_stats()
        return profiled_func

except ImportError:
    def line_profile(func):
        def nothing(*args, **kwargs):
            return func(*args, **kwargs)
        return nothing

#
# Module Profiling
#

if False:
    from line_profiler import LineProfiler
    lpr = LineProfiler()

    # change this to the target file / module to profile
    import lighthouse.metadata as metadata_module
    lpr.add_module(metadata_module)

    # put this code somewhere to dump results:
    #global lpr
    #lpr.enable_by_count()
    #lpr.disable_by_count()
    #lpr.print_stats()
