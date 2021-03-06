#ifndef REVEAL
#define REVEAL
#define QUEUE_BUF 10000

void *aligner(void *arg);

typedef struct
{
    PyObject_HEAD
    char         * T;   //initial input Text
    int32_t          * SA;  //Suffix array
    int32_t          * SAi; //Suffix array
    int32_t          * LCP; //LCP array
    uint16_t          * SO;  //Array indicating for each suffix which sample it originated from (max 2**16 samples!)
    int n;   //lenght of SA and LCP
    int depth; //depth within the hierarchical alignment tree
    int *nsep;    //array of integers pointing to sentinels that seperate samples within the input T
    int nsamples; //number of samples in T
    PyObject * main; //main index
    PyObject * samples; //list of sample names that are contained in the index
    PyObject * nodes; //list of intervals in T that are associated with this index
    PyObject * left;
    //PyObject * leftoffsets;
    PyObject * right;
    //PyObject * rightoffsets;
} RevealIndex;

RevealIndex* newIndex();

typedef struct
{
    int threadid;
    PyObject * mumpicker; //callback function that return the best exact match from a list of exact matches
    PyObject * graphalign; //callback that updates the interval tree and graph for the alignment
} RevealWorker;

typedef struct
{
    int l; //length of the exact match
    int *sp; //array of starting positions
    int n;   //number of samples in which the exact match occurs
    int u;   //whether the match is unique (1) or not (0)
    int score;
    int penalty;
} RevealMultiMUM;

int getbestmum(RevealIndex *index, RevealMultiMUM *mum);
int getbestmultimum(RevealIndex *index, RevealMultiMUM *mmum, int min_n);
PyObject * getmultimums(RevealIndex *index);
PyObject * getmums(RevealIndex *index);

#endif
